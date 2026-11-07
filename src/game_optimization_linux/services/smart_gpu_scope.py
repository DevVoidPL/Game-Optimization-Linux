"""Smart GPU Scope: resource controller and separate GPU-memory observer.

No focus guessing, process migration or repeated dmem writes. A changed low is
evidence of a change, not evidence identifying its writer. Stdlib-only so the
same policy runs in the host bridge outside Flatpak.
"""
from copy import deepcopy
import re
import time
import json
import os
from pathlib import Path
import subprocess
import tempfile
import fcntl
import sys


def background_group(group):
    return isinstance(group, str) and '..' not in group.split('/') and re.search(
        r'/app-gameopti-background-[a-f0-9]+\.scope$', group) is not None


def pressure_value(text):
    match = re.search(r'^some avg10=([0-9.]+)', text, re.M)
    return float(match[1]) if match else None


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
            fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


class ResourceController:
    """CPU weight only: write-ahead ownership, identity + compare-before-restore.

    CPU shares are not limits. No memory/dmem writes, process migration, nice,
    real-time scheduling, IO tuning, freezing or arbitrary service control.
    """
    def __init__(self, backend, game, journal, *, mode='monitor'):
        self.backend, self.game, self.journal = backend, game, Path(journal)
        self.mode = mode
        self.owned = json.loads(self.journal.read_text()) if self.journal.exists() else {}
        self.conflicts, self.errors = [], []
        self.since = None
        self.desired = None
        self.active = False
        self.restored = False
        self.reason = 'observation only'
        self.signals = {}
        self.blocked = set()

    def status(self):
        return dict(mode=self.mode, activePolicies=dict(self.owned), reason=self.reason,
                    restored=self.restored, conflicts=self.conflicts[-16:], errors=self.errors[-16:],
                    signals=self.signals, unsupported=['IO priority: no verified independent device control',
                    'memory tuning: pressure observed, no safe reclaim policy',
                    'focus: compositor API has no verified window PID correlation',
                    'existing/shared background cgroups: unsupported; opt-in isolated launcher required'])

    def restore(self, units=None):
        for unit, entry in list(self.owned.items()):
            if units is not None and unit not in units:
                continue
            try:
                current = self.backend.inspect(unit)
                if current.get('ended'):
                    # The cgroup no longer exists: its runtime CPU setting cannot
                    # survive. Never write to a vanished/recycled scope name.
                    pass
                elif current['identity'] != entry['identity']:
                    self.conflicts.append(unit + ': identity changed; untouched')
                elif not current['safe']:
                    raise ValueError(unit + ': isolation unavailable; restoration pending')
                else:
                    if current['weight'] == entry['applied']:
                        self.backend.set_weight(unit, entry.get('configured', entry['before']))
                        actual = self.backend.inspect(unit)
                        if not actual.get('ended') and (
                                actual['identity'] != entry['identity'] or not actual['safe'] or
                                actual['weight'] != entry['before'] or
                                ('configured' in actual and actual['configured'] !=
                                 entry.get('configured', entry['before']))):
                            raise ValueError(unit + ': CPUWeight restoration readback failed; journal retained')
                    elif current['weight'] != entry['before']:
                        self.conflicts.append(unit + ': external weight change preserved')
                del self.owned[unit]
                save(self.journal, self.owned)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                self.errors.append(str(error))  # retain journal for retry
        self.active = False
        self.restored = not self.owned and not self.conflicts

    def apply(self, unit, weight):
        if unit in self.owned or unit in self.blocked:
            return
        current = self.backend.inspect(unit)
        if not current['safe'] or current['weight'] != 100:
            self.errors.append(unit + ': isolation/custom CPU weight prevents ownership')
            return
        self.owned[unit] = dict(identity=current['identity'], before=current['weight'], applied=weight,
                               configured=current.get('configured', current['weight']))
        self.restored = False
        save(self.journal, self.owned)  # intent survives crash between set-property and response
        self.backend.set_weight(unit, weight)
        actual = self.backend.inspect(unit)
        if actual['identity'] != current['identity'] or actual['weight'] != weight:
            raise ValueError('CPUWeight readback failed: ' + unit)

    def update(self, now, signals, *, focus=None, backgrounds=()):
        self.signals = signals
        game_ended = False
        for unit, entry in list(self.owned.items()):
            try:
                current = self.backend.inspect(unit)
                if current.get('ended'):
                    game_ended |= unit == self.game
                    del self.owned[unit]
                    save(self.journal, self.owned)
                elif (current['identity'] != entry['identity'] or
                      (current['safe'] and current['weight'] != entry['applied'])):
                    self.conflicts.append(unit + ': policy changed externally; relinquished')
                    self.blocked.add(unit)
                    del self.owned[unit]
                    save(self.journal, self.owned)
                elif not current['safe']:
                    raise ValueError(unit + ': isolation unavailable; restoration pending')
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                self.errors.append(str(error))
                self.restore()
                return self.status()
        if game_ended:
            self.restore()
            self.reason = ('game scope ended; background policies restored' if self.restored else
                           'game scope ended; restoration incomplete or conflicted')
            return self.status()
        if self.game in self.blocked:
            self.restore()
            self.reason = ('game ownership lost; background restoration pending' if self.owned else
                           'game ownership lost; external policy preserved')
            return self.status()
        cpu, usage = signals.get('cpuPressure'), signals.get('gameCpu')
        want = (self.mode in {'balanced', 'performance'} and focus is not False and
                cpu is not None and usage is not None and cpu >= (10 if self.mode == 'balanced' else 5)
                and usage >= .25 and not signals.get('throttleDelta'))
        if want != self.desired:
            self.desired, self.since = want, now
        delay = 6 if want else 12
        if self.mode == 'monitor':
            self.restore()
        elif now - self.since >= delay:
            if want:
                self.reason = 'sustained CPU PSI and active game CPU demand'
                try:
                    self.apply(self.game, 200 if self.mode == 'balanced' else 400)
                    # Only reduce background shares after owning the game policy.
                    if self.game in self.owned:
                        for unit in backgrounds:
                            self.apply(unit, 50 if self.mode == 'balanced' else 25)
                        # Workloads no longer contending must be restored too.
                        removed = set(self.owned) - {self.game, *backgrounds}
                        if removed:
                            self.restore(removed)
                    self.active = bool(self.owned)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    self.errors.append(str(error))
                    self.restore()
            else:
                self.reason = 'focus lost, contention absent or metrics unavailable'
                self.restore()
        return self.status()


CG = Path('/sys/fs/cgroup')


def text(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ''


def systemctl(*args):
    result = subprocess.run(['/usr/bin/systemctl', '--user', *args], capture_output=True,
                            text=True, timeout=5, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'user systemd unavailable')
    return result.stdout


def runtime_root():
    root = Path('/run/user') / str(os.getuid()) / 'gameopti-resource'
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('Unsafe resource runtime directory')
    return root


def critical(group):
    """Fail closed for compositor/audio/desktop executables anywhere in scope."""
    directory = CG / group.lstrip('/')
    paths = [directory / 'cgroup.procs', *directory.rglob('cgroup.procs')]
    pids = {v for path in paths for v in text(path).split() if v.isdigit()}
    if not pids or len(pids) > 256:
        return True
    for pid in pids:
        try:
            stat = Path('/proc/' + pid).stat()
            name = Path(os.readlink('/proc/' + pid + '/exe')).name.lower()
        except OSError:
            return True
        if stat.st_uid != os.getuid() or any(token in name for token in (
                'aqueous', 'kwin', 'gamescope', 'pipewire', 'wireplumber', 'pulseaudio',
                'plasmashell', 'xdg-', 'dbus', 'systemd', 'input', 'gnome-shell', 'sway', 'weston')):
            return True
    return False


class SystemdBackend:
    def __init__(self, game_group):
        if not (owned_group(game_group) or background_group(game_group)):
            raise ValueError('Not a GameOpti dedicated scope')
        self.group = game_group

    def inspect(self, unit):
        expected = self.group if unit == Path(self.group).name else str(Path(self.group).parent / unit)
        if not (owned_group(expected) or background_group(expected)):
            raise ValueError('Not a dedicated resource scope')
        directory = CG / expected.lstrip('/')
        ended = dict(identity='', weight=-1, safe=False, configured='', ended=True)
        try:
            directory.stat()  # exists() masks permission/IO errors on Python 3.14
        except FileNotFoundError:
            return ended
        try:
            values = dict(line.split('=', 1) for line in systemctl('show', unit,
                '-p', 'ControlGroup', '-p', 'InvocationID', '-p', 'CPUWeight', '-p', 'ActiveState').splitlines() if '=' in line)
        except (OSError, ValueError, subprocess.SubprocessError):
            try:
                directory.stat()
            except FileNotFoundError:
                return ended  # collected between cgroup stat and systemctl show
            raise
        group = values.get('ControlGroup', '')
        valid = group == self.group if unit == Path(self.group).name else background_group(group)
        # Exact sibling scopes are needed for relative CPU weights to compete.
        valid &= Path(group).parent == Path(self.group).parent
        valid &= values.get('ActiveState') == 'active' and bool(values.get('InvocationID'))
        valid &= group.startswith(f'/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/')
        try:
            weight = int((directory / 'cpu.weight').read_text().strip())
        except FileNotFoundError:
            try:
                directory.stat()
            except FileNotFoundError:
                return ended
            raise
        configured = values.get('CPUWeight', '')
        if configured in {'18446744073709551615', '[not set]'}:
            configured = ''  # systemd UINT64_MAX means unset, not explicit 100
        return dict(identity=values.get('InvocationID', '') + ':' + group, configured=configured,
                    weight=weight, safe=bool(valid and not critical(group)))

    def set_weight(self, unit, weight):
        if not self.inspect(unit)['safe']:
            raise ValueError('Scope no longer isolated: ' + unit)
        systemctl('set-property', '--runtime', unit, 'CPUWeight=' + str(weight))


class Sampler:
    def __init__(self):
        self.previous = {}

    def sample(self, group, now):
        directory = CG / group.lstrip('/')
        stats = dict(re.findall(r'(\w+) (\d+)', text(directory / 'cpu.stat')))
        usage = int(stats['usage_usec']) if 'usage_usec' in stats else None
        throttled = int(stats['nr_throttled']) if 'nr_throttled' in stats else None
        old = self.previous.get(group)
        cpu = ((usage - old[1]) / ((now - old[0]) * 1e6)
               if old and usage is not None and old[1] is not None and now > old[0] and usage >= old[1] else None)
        throttle_delta = max(0, throttled - old[2]) if old and throttled is not None and old[2] is not None else None
        self.previous[group] = (now, usage, throttled)
        return dict(cpuPressure=pressure_value(text(directory / 'cpu.pressure')), gameCpu=cpu,
                    memoryPressure=pressure_value(text(directory / 'memory.pressure')),
                    ioPressure=pressure_value(text(directory / 'io.pressure')),
                    throttled=throttled, throttleDelta=throttle_delta)


def available():
    return (CG / 'cgroup.controllers').exists() and 'cpu' in text(CG / 'cgroup.controllers').split() and Path('/usr/bin/systemd-run').exists()


def start_controller(group, profile, status):
    if not owned_group(group) or not available():
        return
    token = Path(group).stem.removeprefix('app-gameopti-')
    journal = runtime_root() / (token + '.json')
    script = str(Path(__file__).resolve())
    # Independent user service survives GUI/wrapper exit; stop-post runs even on
    # SIGKILL/start failure. Boot/logout destroys all user scopes and runtime state.
    recovery = '/usr/bin/python3 ' + script + ' recover ' + group + ' ' + str(journal)
    if any(c.isspace() or c in '%$\\"\'' for c in script):
        raise ValueError('Resource bridge path cannot be represented safely in systemd ExecStopPost')
    result = subprocess.run(['/usr/bin/systemd-run', '--user', '--collect', '--quiet',
        '--unit=app-gameopti-controller-' + token, '--property=TimeoutStopSec=30',
        '--property=ExecStopPost=' + recovery, '--', '/usr/bin/python3', script, 'watch',
        group, str(journal), str(profile), str(status)], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Cannot start resource restoration service')


def watch(group, journal, profile, status):
    if not owned_group(group):
        raise ValueError('Not a dedicated game scope')
    root = runtime_root()
    # One active resource controller per user. Multiple games fail closed rather
    # than letting two journals claim the same background settings.
    with (root / 'controller.lock').open('a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            save(status, {'errors': ['Another game owns adaptive resource control'], 'mode': 'monitor'})
            return
        backend = SystemdBackend(group)
        controller = ResourceController(backend, Path(group).name, journal)
        controller.restore()
        if controller.owned:
            save(status, {**controller.status(), 'reason': 'Previous resource restoration is unresolved'})
            return
        # Recover an older killed controller before acquiring new ownership.
        for old in root.glob('*.json'):
            if old == Path(journal) or old.name.startswith('background-'):
                continue
            entries = json.loads(text(old) or '{}')
            game_entries = [entry for unit, entry in entries.items()
                            if isinstance(entry, dict) and unit.startswith('app-gameopti-')
                            and not unit.startswith('app-gameopti-background-')]
            if game_entries:
                old_group = game_entries[0].get('identity', '').partition(':')[2]
                if owned_group(old_group):
                    recovery = ResourceController(SystemdBackend(old_group), Path(old_group).name, old)
                    recovery.restore()
                    if recovery.owned:
                        save(status, {'errors': ['Previous resource restoration is unresolved'], 'mode': 'monitor'})
                        return
        sampler = Sampler()
        started, cpu_start, samples = time.monotonic(), time.process_time(), 0
        try:
            while 'populated 1' in text(CG / group.lstrip('/') / 'cgroup.events'):
                now = time.monotonic()
                try:
                    settings = json.loads(text(profile) or '{}')
                    # The independent watchdog never relies on the wrapper's PID.
                    # cgroup.events keeps Proton descendants alive after parent exit.
                    mode = settings.get('smartMode', 'monitor') if settings.get('smartScope') else 'monitor'
                    if mode != controller.mode:
                        controller.restore()
                        controller.mode = mode
                    signals = sampler.sample(group, now)
                    backgrounds = []
                    for registration in sorted(root.glob('background-*.json'))[:32]:
                        record = json.loads(text(registration) or '{}')
                        bg = record.get('group', '')
                        if not background_group(bg):
                            continue
                        if 'populated 1' not in text(CG / bg.lstrip('/') / 'cgroup.events'):
                            registration.unlink(missing_ok=True)
                            continue
                        bg_signals = sampler.sample(bg, now)
                        if (bg_signals['gameCpu'] is not None and bg_signals['gameCpu'] >= .25
                                and bg_signals['cpuPressure'] is not None and bg_signals['cpuPressure'] >= 5):
                            unit = Path(bg).name
                            if backend.inspect(unit)['identity'] == record.get('identity'):
                                backgrounds.append(unit)
                    value = controller.update(now, signals, backgrounds=backgrounds)
                    samples += 1
                    value.update(observedAt=time.time(), sessionIdentified=True,
                        controllerCpuSeconds=time.process_time() - cpu_start,
                        controllerElapsedSeconds=now - started, samples=samples,
                        aqueousConnected=False, focus=None)
                    window = json.loads(text(Path(profile).with_name('scope-window.json')) or '{}')
                    if 0 <= time.time() - float(window.get('observedAt', 0)) <= 5:
                        value['aqueousConnected'] = bool(window.get('available'))
                        value['compositorObservation'] = window
                    save(status, value)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    controller.errors.append(str(error))
                    controller.restore()
                    save(status, controller.status())
                time.sleep(3)
        finally:
            controller.restore()
            if controller.owned:
                controller.reason = 'game controller finished; restoration pending'
            elif controller.conflicts:
                controller.reason = 'game controller finished; external policy preserved'
            else:
                controller.reason = 'game controller finished; owned policies restored or scopes ended'
            save(status, {**controller.status(), 'state': 'finished', 'observedAt': time.time()})


def background_launch(argv):
    """Explicit opt-in; never adopt/migrate an already running shared workload."""
    if not argv or Path(argv[0]).name.lower() not in {'firefox', 'chromium', 'discord', 'Discord'}:
        raise ValueError('Only explicitly opted-in browser/Discord launches are supported')
    token = os.urandom(8).hex()
    return subprocess.call(['/usr/bin/systemd-run', '--user', '--scope', '--collect', '--quiet',
        '--unit=app-gameopti-background-' + token, '--slice=app.slice', '--expand-environment=no',
        '--', '/usr/bin/python3', str(Path(__file__).resolve()), 'background-enter', *argv])


def main(argv):
    if os.getuid() == 0:
        raise ValueError('Resource control must run as the login user')
    if argv[0] == 'watch':
        watch(*argv[1:])
    elif argv[0] == 'recover':
        group, journal = argv[1:]
        with (runtime_root() / 'controller.lock').open('a') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0  # next watcher recovers old journals under the same lock
            recovery = ResourceController(SystemdBackend(group), Path(group).name, journal)
            recovery.restore()
            if recovery.owned or recovery.conflicts:
                raise ValueError('Resource restoration incomplete or conflicted; inspect retained journal and scope state')
    elif argv[0] == 'background':
        return background_launch(argv[1:])
    elif argv[0] == 'background-enter':
        group = text(f'/proc/{os.getpid()}/cgroup').strip().removeprefix('0::')
        if not background_group(group):
            raise ValueError('Dedicated opt-in scope missing')
        identity = SystemdBackend(group).inspect(Path(group).name)['identity']
        save(runtime_root() / ('background-' + os.urandom(8).hex() + '.json'), dict(group=group, identity=identity))
        os.execvp(argv[1], argv[1:])
    return 0


def owned_group(group):
    return (isinstance(group, str) and group.startswith("/") and
            ".." not in group.split("/") and
            re.search(r"/app-gameopti-[a-f0-9]+-[a-f0-9]+\.scope$", group) is not None)


def scope_samples(host, group, region):
    """Follow cgroup descendants, including reparented Wine/launcher children."""
    if not owned_group(group):
        return []
    directory = host.CG / group.lstrip("/")
    pids = set()
    for path in [directory / "cgroup.procs", *directory.rglob("cgroup.procs")]:
        pids.update(int(value) for value in host.read(path).split() if value.isdigit())
    return [host.snapshot(pid, region) for pid in sorted(pids)[:256]]


def limits(chain, region):
    return {item["cgroup"]: item.get("low", {})[region] for item in chain
            if item.get("cgroup") and region in item.get("low", {})}


class ScopeObserver:
    def __init__(self, group, region, baseline, *, requested):
        self.group, self.region, self.requested = group, region, requested
        self.baseline = limits(baseline, region)
        self.transitions = []
        self.state = None

    def _result(self, state, **values):
        now = time.time()
        if state != self.state:
            self.transitions.append({"from": self.state, "to": state, "at": now})
            self.transitions = self.transitions[-32:]
            self.state = state
        return {"state": state, "observedAt": now, "action": "observe_only",
                "writer": "unknown", "protectionRequested": self.requested,
                "region": self.region, "cgroup": self.group,
                "transitions": deepcopy(self.transitions), **values}

    def update(self, samples, *, managers=(), region_available=True):
        gpu = [item for item in samples if item.get("drmClients")]
        changes = []
        for item in samples:
            for group, value in limits(item.get("chain", []), self.region).items():
                if group in self.baseline and value != self.baseline[group]:
                    change = {"cgroup": group, "before": self.baseline[group], "after": value}
                    if change not in changes:
                        changes.append(change)
        escaped = any(item.get("cgroup") != self.group and
                      not item.get("cgroup", "").startswith(self.group + "/") for item in samples)
        if not owned_group(self.group):
            state = "degraded"
        elif not self.region or not region_available:
            state = "unsupported"
        elif len(samples) >= 256:
            state = "degraded"  # bounded scan may have omitted another GPU client
        elif escaped:
            state = "degraded"
        elif changes:
            state = "externally_modified"
        elif not gpu:
            state = "waiting_for_gpu_process"
        elif all(item.get("protectionVerified") for item in gpu):
            state = "active_verified"
        else:
            state = "active_unverified"
        return self._result(state, changes=changes, managers=sorted(set(managers)),
                            processes=[{key: item.get(key) for key in
                                ("pid", "startTicks", "command", "cgroup", "protectionVerified")}
                                for item in gpu],
                            limitation="dmem.low is best-effort protection, not a VRAM reservation or performance measurement")

    def finish(self, exit_code):
        return self._result("finished", exitCode=exit_code, protectionVerified=False)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))