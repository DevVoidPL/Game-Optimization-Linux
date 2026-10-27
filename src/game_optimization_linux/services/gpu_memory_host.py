"""Host bridge (stdlib only). Games and telemetry always run as the login user.

The only privileged verbs prepare/restore Valve's packaged dmemcg-booster.
No custom scheduler, kernel, VRAM ceiling, driver option or global game env.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

CG = Path("/sys/fs/cgroup")
SYSTEM_UNIT = "dmemcg-booster-system.service"
USER_UNIT = "dmemcg-booster-user.service"
HELPER = Path("/usr/libexec/game-optimization-linux-gpu-memory")
RECORD = Path("/var/lib/game-optimization-linux/gpu-memory-setup.json")


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def pairs(path):
    result = {}
    for line in read(path).splitlines():
        fields = line.split()
        if len(fields) == 2:
            result[fields[0]] = int(fields[1]) if fields[1].isdigit() else fields[1]
    return result


def command(argv, *, check=False, timeout=20):
    return subprocess.run(argv, capture_output=True, text=True, check=check, timeout=timeout)


def user_command(uid, argv):
    if os.getuid() == uid:
        return command(argv)
    import pwd
    name = pwd.getpwuid(uid).pw_name
    return command(["/usr/bin/runuser", "-u", name, "--", "/usr/bin/env",
                    f"XDG_RUNTIME_DIR=/run/user/{uid}",
                    f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus", *argv])


def unit_state(unit, uid=None):
    argv = ["/usr/bin/systemctl", *( ["--user"] if uid is not None else [])]
    run = (lambda args: user_command(uid, args)) if uid is not None else command
    return {"active": run([*argv, "is-active", unit]).stdout.strip(),
            "enabled": run([*argv, "is-enabled", unit]).stdout.strip()}


def cgroup(pid):
    return next((line[3:] for line in read(f"/proc/{pid}/cgroup").splitlines()
                 if line.startswith("0::") and "(deleted)" not in line), "")


def gpu_devices():
    result = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if not re.fullmatch(r"card\d+", card.name) or not (card / "device").exists():
            continue
        device = (card / "device").resolve()
        vendor = read(device / "vendor")
        data = {"pci": device.name, "vendor": {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}.get(vendor, vendor),
                "driver": (device / "driver").resolve().name, "device": str(device),
                "vendorId": vendor, "deviceId": read(device / "device")}
        for key, file in (("vramTotal", "mem_info_vram_total"), ("visibleVram", "mem_info_vis_vram_total"),
                          ("vramUsed", "mem_info_vram_used"), ("gttUsed", "mem_info_gtt_used")):
            value = read(device / file)
            data[key] = int(value) if value.isdigit() else None
        capacities = [v for k, v in pairs(CG / "dmem.capacity").items() if device.name in k and isinstance(v, int)]
        if not data["vramTotal"] and capacities:
            data["vramTotal"] = max(capacities)
        if data["visibleVram"] is None:
            bars = []
            for row in read(device / "resource").splitlines():
                try:
                    start, end, flags = (int(v, 16) for v in row.split())
                    if flags & 0x2000 and end >= start and start:
                        bars.append(end - start + 1)
                except ValueError:
                    pass
            data["visibleVram"] = max(bars) if bars else None
        result.append(data)
    # Vulkan device type disambiguates APU reserved VRAM from a discrete GPU.
    try:
        summary = command(["vulkaninfo", "--summary"], timeout=12).stdout
        for block in re.split(r"GPU\d+:", summary)[1:]:
            vid = re.search(r"vendorID\s*=\s*(0x[0-9a-fA-F]+)", block)
            did = re.search(r"deviceID\s*=\s*(0x[0-9a-fA-F]+)", block)
            name = re.search(r"deviceName\s*=\s*(.*)", block)
            typ = re.search(r"deviceType\s*=\s*(.*)", block)
            for data in result:
                if (vid and did and data["vendorId"] and data["deviceId"] and int(vid.group(1), 16) == int(data["vendorId"], 16)
                        and int(did.group(1), 16) == int(data["deviceId"], 16)):
                    data.update(name=name.group(1) if name else "", type=typ.group(1) if typ else "")
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def probe():
    uid = os.getuid()
    manager = command(["systemctl", "--user", "show", "-p", "ControlGroup", "--value"]).stdout.strip()
    region = pairs(CG / "dmem.capacity")
    hierarchy = [CG / "user.slice", CG / f"user.slice/user-{uid}.slice",
                 CG / manager.lstrip("/"), CG / manager.lstrip("/") / "app.slice"]
    ready_regions = [key for key in region if all(isinstance(pairs(p / "dmem.low").get(key), int)
                     and pairs(p / "dmem.low")[key] > 0 for p in hierarchy)]
    btf = Path("/sys/kernel/btf/ttm")
    try:
        data = btf.read_bytes()
    except OSError:
        data = b""
    # A build-specific backport signature, not an inference from uname.
    ttm = all(name in data for name in (b"ttm_bo_alloc_state\0", b"may_try_low\0", b"ttm_bo_alloc_at_place\0", b"charge_pool\0"))
    return {"kernel": os.uname().release, "regions": region, "gpus": gpu_devices(),
            "controllers": read(CG / "cgroup.controllers").split(), "managerCgroup": manager,
            "readyRegions": ready_regions,
            "delegated": "dmem" in read(CG / manager.lstrip("/") / "cgroup.controllers").split(),
            "writable": os.access(CG / manager.lstrip("/") / "app.slice", os.W_OK),
            "systemBooster": unit_state(SYSTEM_UNIT), "userBooster": unit_state(USER_UNIT, uid),
            "plasmaBooster": unit_state("plasma-foreground-booster.service", uid),
            "boosterInstalled": bool(shutil.which("dmemcg-booster")),
            "boosterPackage": command(["pacman", "-Q", "dmemcg-booster"]).stdout.strip() if shutil.which("pacman") else "",
            "helperInstalled": HELPER.exists(), "managedSetup": RECORD.exists(),
            "ttmProtectionEvidence": "cachyos-backport-btf" if ttm else "unverified",
            "ttmEvidenceDetail": "ttm_bo_alloc_state + may_try_low + ttm_bo_alloc_at_place (CachyOS backport / upstream TTM)" if ttm else "Running module BTF does not establish the eviction patchset"}


def snapshot(pid, region=""):
    relative = cgroup(pid)
    group = CG / relative.lstrip("/")
    capacities = pairs(CG / "dmem.capacity")
    chain = []
    current = group
    while current != CG and current.is_relative_to(CG):
        chain.append({"cgroup": "/" + str(current.relative_to(CG)), "low": pairs(current / "dmem.low"),
                      "min": pairs(current / "dmem.min"), "max": pairs(current / "dmem.max"),
                      "current": pairs(current / "dmem.current")})
        current = current.parent
    fdinfo = {}
    try:
        for path in Path(f"/proc/{pid}/fdinfo").iterdir():
            text = read(path)
            client = re.search(r"drm-client-id:\s*(\d+)", text)
            if client:
                pci = re.search(r"drm-pdev:\s*(\S+)", text)
                identity = (pci.group(1) if pci else "unknown") + ":" + client.group(1)
                fdinfo[identity] = {k: int(v) * 1024 for k, v in re.findall(r"(drm-(?:resident|memory|shared)-[^:]+):\s*(\d+)\s*KiB", text)}
    except OSError:
        pass
    target = [region] if region else list(capacities)
    low_chain = bool(relative and chain and target) and all(
        all(isinstance(item["low"].get(key), int) and item["low"][key] > 0 for item in chain)
        for key in target)
    protected = bool(low_chain and fdinfo and chain and
                     any(isinstance(chain[0]["current"].get(key), int) and chain[0]["current"][key] > 0 for key in target))
    config = next((v.partition("=")[2] for v in read(f"/proc/{pid}/environ").split("\0")
                   if v.startswith("VKD3D_CONFIG=")), "")
    return {"pid": pid, "startTicks": read(f"/proc/{pid}/stat").rpartition(")")[2].split()[19:20],
            "command": read(f"/proc/{pid}/comm"), "cgroup": relative, "chain": chain,
            "protectionVerified": protected, "lowChainVerified": bool(low_chain), "regions": capacities, "drmClients": fdinfo,
            "vkd3dNoUploadHvv": "no_upload_hvv" in re.split(r"[,;]", config),
            "ram": read(f"/proc/{pid}/status"), "memoryPressure": read("/proc/pressure/memory")}


def protection_observation(sample, region):
    """Keep inaccessible/uncharged GPU evidence distinct from an observed zero low."""
    if sample.get("protectionVerified"):
        return "protected"
    chain = sample.get("chain", [])
    if region and sample.get("drmClients") and chain and all(region in g.get("low", {}) for g in chain):
        if any(g["low"][region] == 0 for g in chain):
            return "unprotected"
    return "unknown"


def target_process(pid, executable):
    if not executable:
        return False
    return any(line.split(maxsplit=5)[-1].removesuffix(" (deleted)") == executable
               for line in read(f"/proc/{pid}/maps").splitlines() if len(line.split(maxsplit=5)) == 6)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


ACTIVE_SESSION = {"starting", "observing", "protected_scope_started"}
TERMINAL_SESSION = {"exited", "interrupted"}
_SCOPE = re.compile(r"/app-gameopti-[a-f0-9]+-[a-f0-9]+\.scope$")


def start_ticks(pid):
    """Process start time (clock ticks since boot) as recorded in sessions; [] if gone."""
    return read(f"/proc/{pid}/stat").rpartition(")")[2].split()[19:20] if pid > 0 else []


def _identity_alive(pid, ticks):
    """True/False when /proc proves it, None when the record lacks the evidence."""
    if not isinstance(pid, int) or pid <= 0 or not isinstance(ticks, list) or not ticks:
        return None
    current = start_ticks(pid)
    return bool(current) and current == ticks  # different ticks: the PID was reused


def _scope_state(group):
    if not isinstance(group, str) or not _SCOPE.search(group):
        return "not_applicable"
    directory = CG / group.lstrip("/")
    if not directory.exists():
        return "gone"
    populated = pairs(directory / "cgroup.events").get("populated")
    return "populated" if populated == 1 else "empty" if populated == 0 else "unknown"


def reconcile_session(output, key, *, now=None, replace=os.replace):
    """Read-time recovery of a session record whose wrapper never finalized it.

    Only reads /proc and cgroup files; never creates a scope or writes dmem.
    A terminal state is persisted atomically only when the game process, the
    wrapper and the private scope are all proven gone. Returns a view:
    ``state`` is running, exited, interrupted, unverified, missing, malformed
    or foreign; ``record`` is the (possibly reconciled) record.
    """
    path = Path(output)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {"state": "missing", "record": {}, "persisted": False}
    except OSError as error:
        return {"state": "unverified", "record": {}, "persisted": False, "error": str(error)}
    try:
        record = json.loads(raw)
    except ValueError:
        return {"state": "malformed", "record": {}, "persisted": False}
    if not isinstance(record, dict) or record.get("key") != key:
        return {"state": "foreign", "record": {}, "persisted": False}
    status = record.get("status")
    if status in TERMINAL_SESSION:
        return {"state": status, "record": record, "persisted": False}
    if status not in ACTIVE_SESSION:
        return {"state": "malformed", "record": {}, "persisted": False}
    game = _identity_alive(record.get("pid"), record.get("startTicks")) if status != "starting" else False
    wrapper = _identity_alive(record.get("wrapperPid"), record.get("wrapperStartTicks"))
    scope = _scope_state(record.get("cgroup"))
    evidence = {"gameAlive": game, "wrapperAlive": wrapper, "scope": scope}
    if game is True or wrapper is True or scope == "populated":
        return {"state": "running", "record": record, "persisted": False, "evidence": evidence}
    if status == "starting":
        if wrapper is not False:  # an old record without the wrapper identity
            return {"state": "unverified", "record": record, "persisted": False, "evidence": evidence}
        reason = "launch_interrupted_before_game"
    elif game is None or scope == "unknown":
        return {"state": "unverified", "record": record, "persisted": False, "evidence": evidence}
    else:
        reason = "wrapper_interrupted_after_launch" if record.get("wrapperSignal") else "wrapper_ended_without_final_state"
    final = {**record, "status": "interrupted", "statusBeforeReconciliation": status,
             "reconciledAt": now or datetime.now(timezone.utc).isoformat(),
             "reconciliation": {"reason": reason, **evidence},
             # Last verified samples (chain, regions, drmClients...) stay as recorded.
             "lastProtectionVerified": bool(record.get("protectionVerified")),
             "protectionVerified": False}
    tmp = path.with_name(path.name + f".{os.getpid()}.reconcile.tmp")
    try:
        tmp.write_text(json.dumps(final, indent=2) + "\n")
        tmp.chmod(0o600)
        if path.read_text() != raw:  # the wrapper finalized it meanwhile
            tmp.unlink()
            return reconcile_session(output, key, now=now, replace=replace)
        replace(tmp, path)
    except OSError as error:
        tmp.unlink(missing_ok=True)
        return {"state": "interrupted", "record": final, "persisted": False, "evidence": evidence, "error": str(error)}
    return {"state": "interrupted", "record": final, "persisted": True, "evidence": evidence}


def kernel_gpu_errors(started):
    try:
        since = datetime.fromisoformat(started).strftime("%Y-%m-%d %H:%M:%S UTC")
        result = command(["journalctl", "-k", "--since", since, "--priority=err", "--no-pager", "-o", "json", "-n", "200"], timeout=8)
        if result.returncode or "not seeing" in result.stderr or "permission" in result.stderr.lower():
            return None
        errors = []
        for line in result.stdout.splitlines():
            value = json.loads(line)
            message = str(value.get("MESSAGE", ""))
            if re.search(r"amdgpu|\bdrm\b|\bNVRM\b|\bi915\b|\bxe\b", message, re.I):
                errors.append(message[:2048])
        return errors[:32]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def scope_populated(output, unit):
    try:
        record = json.loads(read(output) or "{}")
        group = record.get("cgroup", "")
        if not group.endswith("/" + unit):
            return False
        return pairs(CG / group.lstrip("/") / "cgroup.events").get("populated") == 1
    except (OSError, ValueError):
        return False


def run_scope(args):
    if os.getuid() == 0:
        raise ValueError("The game must run unprivileged")
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not argv:
        raise ValueError("Missing game command")
    unit = "app-gameopti-" + hashlib.sha256(args.key.encode()).hexdigest()[:12] + "-" + os.urandom(5).hex() + ".scope"
    # systemd owns lifetime and descendants. Create the scope before Vulkan/Wine allocation.
    child = [sys.executable, str(Path(__file__).resolve()), "enter", "--key", args.key,
             "--region", args.region, "--output", args.output, "--protect", str(args.protect), "--", *argv]
    scoped = bool(args.protect or (shutil.which("systemd-run") and Path("/run/systemd/system").exists()))
    # systemd >= 254 expands $NAME in the command line: keep game arguments literal.
    try:
        literal = ["--expand-environment=no"] if "--expand-environment" in subprocess.run(
            ["systemd-run", "--help"], capture_output=True, text=True, timeout=10, check=False).stdout else []
    except (OSError, subprocess.TimeoutExpired):
        literal = []
    launch = (["systemd-run", "--user", "--scope", "--collect", "--quiet", "--unit=" + unit,
               "--slice=app.slice", *literal, "--", *child] if scoped else argv)
    started = datetime.now(timezone.utc).isoformat()
    atomic(args.output, {"key": args.key, "createdAt": started, "status": "starting", "protected": bool(args.protect),
                         "wrapperPid": os.getpid(), "wrapperStartTicks": start_ticks(os.getpid())})
    received = []

    def on_signal(number, _frame):
        received.append(number)
        raise KeyboardInterrupt

    previous_term = signal.signal(signal.SIGTERM, on_signal)
    process = subprocess.Popen(launch)
    if not scoped:
        atomic(args.output, {**snapshot(process.pid, args.region), "key": args.key, "createdAt": started,
                             "status": "observing", "protected": False})
    metrics = open(args.metrics, "a", buffering=1) if args.metrics else None
    if metrics:
        os.chmod(args.metrics, 0o600)
    aggregates = {"samples": 0, "protectedSamples": 0, "peakVramDevice": None,
                  "targetSamples": 0, "targetProtection": {"protected": 0, "unprotected": 0, "unknown": 0},
                  "peakGttDevice": None, "peakRssSum": None, "peakCgroupVram": None,
                  "fullPsiTotalStart": None, "fullPsiTotalEnd": None, "drmResidencyAvailable": False}
    observed_no_upload = False
    target_no_upload = set()
    telemetry_errors = []
    waited_for_descendants = False
    try:
        while process.poll() is None or (scoped and scope_populated(args.output, unit)):
            waited_for_descendants |= process.poll() is not None
            if metrics:
                try:
                    record = json.loads(read(args.output))
                    group = CG / record.get("cgroup", "").lstrip("/")
                    pids = set()
                    if scoped and record.get("cgroup"):
                        for path in [group / "cgroup.procs", *group.rglob("cgroup.procs")]:
                            pids.update(int(v) for v in read(path).split() if v.isdigit())
                    else:
                        pids = descendants(process.pid)
                    clients = {}; rss = 0; samples = []
                    for pid in sorted(pids)[:128]:
                        sample = snapshot(pid, args.region)
                        clients.update(sample["drmClients"])
                        value = re.search(r"VmRSS:\s*(\d+) kB", sample.pop("ram"))
                        rss += int(value.group(1)) * 1024 if value else 0
                        if sample["drmClients"]:
                            samples.append(sample)
                            observed_no_upload |= sample["vkd3dNoUploadHvv"]
                            if target_process(pid, args.target_executable):
                                sample["targetGame"] = True
                                aggregates["targetSamples"] += 1
                                aggregates["targetProtection"][protection_observation(sample, args.region)] += 1
                                target_no_upload.add(sample["vkd3dNoUploadHvv"])
                    gpu_counters = system_counters()
                    pressure = read("/proc/pressure/memory")
                    aggregates["samples"] += 1
                    aggregates["protectedSamples"] += int(bool(samples) and all(s["protectionVerified"] for s in samples))
                    aggregates["drmResidencyAvailable"] |= any("drm-resident-vram" in c for c in clients.values())
                    totals = {"peakRssSum": rss}
                    for key, source in (("peakVramDevice", "vramUsed"), ("peakGttDevice", "gttUsed")):
                        values = [g[source] for g in gpu_counters if g[source] is not None]
                        if values:
                            totals[key] = sum(values)
                    charged = pairs(group / "dmem.current") if scoped and record.get("cgroup") else {}
                    if charged:
                        totals["peakCgroupVram"] = sum(v for v in charged.values() if isinstance(v, int))
                    for name, value in totals.items():
                        aggregates[name] = max(aggregates[name] or 0, value)
                    psi = re.search(r"full .*total=(\d+)", pressure)
                    if psi:
                        if aggregates["fullPsiTotalStart"] is None:
                            aggregates["fullPsiTotalStart"] = int(psi.group(1))
                        aggregates["fullPsiTotalEnd"] = int(psi.group(1))
                    metrics.write(json.dumps({"time": time.time(), "profileKey": args.key,
                        "protectionRequested": bool(args.protect), "gpuProcesses": samples,
                        "drmClients": clients, "processRssSum": rss,
                        "gpus": gpu_counters, "memoryPressure": pressure, "cgroupCurrent": charged,
                        "vulkanBudget": None}) + "\n")
                except (OSError, ValueError) as error:
                    if str(error) not in telemetry_errors:
                        telemetry_errors.append(str(error))
            time.sleep(1)
        result = process.wait()
        record = json.loads(read(args.output) or "{}")
        atomic(args.output, {**record, "status": "exited", "exitCode": result,
                             "finishedAt": datetime.now(timezone.utc).isoformat()})
        if metrics:
            atomic(Path(args.metrics).with_name("gpu-memory-summary.json"), {
                **aggregates, "key": args.key, "createdAt": started, "exitCode": result,
                "priorityRequested": bool(args.protect), "errors": telemetry_errors,
                "vkd3dNoUploadHvv": observed_no_upload,
                "targetExecutable": args.target_executable,
                "targetNoUploadHvv": next(iter(target_no_upload)) if len(target_no_upload) == 1 else None,
                "waitedForScopeDescendants": waited_for_descendants,
                "kernelGpuErrors": kernel_gpu_errors(started),
                "vulkanBudget": None, "deviceCountersIncludeOtherApps": True,
                "rssSumMayDoubleCountSharedPages": True})
        return result
    except KeyboardInterrupt:
        # The game may still run in its scope: record only that the wrapper was
        # interrupted; reconcile_session() writes the terminal state later.
        try:
            record = json.loads(read(args.output) or "{}")
            if record.get("key") == args.key and record.get("status") in ACTIVE_SESSION:
                atomic(args.output, {**record, "wrapperSignal": signal.Signals(received[0]).name if received else "SIGINT",
                                     "wrapperInterruptedAt": datetime.now(timezone.utc).isoformat()})
        except (OSError, ValueError):
            pass
        if received and received[0] == signal.SIGTERM:
            # Keep the caller-visible SIGTERM exit status.
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if metrics:
            metrics.close()


def descendants(pid):
    result = {pid}
    for _ in range(12):
        added = set()
        for parent in result:
            for task in Path(f"/proc/{parent}/task").glob("*/children"):
                added.update(int(v) for v in read(task).split() if v.isdigit())
        if added <= result:
            break
        result.update(added)
    return result


def system_counters():
    result = []
    for card in Path("/sys/class/drm").glob("card[0-9]*"):
        if re.fullmatch(r"card\d+", card.name):
            device = (card / "device").resolve()
            result.append({"pci": device.name, **{key: int(read(device / name)) if read(device / name).isdigit() else None
                for key, name in (("vramUsed", "mem_info_vram_used"), ("gttUsed", "mem_info_gtt_used"),
                                  ("vramTotal", "mem_info_vram_total"))}})
    return result


def enter(args):
    if os.getuid() == 0:
        raise ValueError("The game must run unprivileged")
    relative = cgroup(os.getpid())
    if not re.search(r"/app-gameopti-[a-f0-9]+-[a-f0-9]+\.scope$", relative):
        raise ValueError("Not inside the private GameOpti systemd scope")
    capacities = pairs(CG / "dmem.capacity")
    selected = {args.region: capacities[args.region]} if args.region in capacities else {}
    if not selected and args.protect:
        raise ValueError("Selected GPU memory region is unavailable")
    low = CG / relative.lstrip("/") / "dmem.low"
    deadline = time.monotonic() + 8
    while args.protect and not low.exists() and time.monotonic() < deadline:
        time.sleep(.1)
    for key, value in selected.items() if args.protect else []:
        low.write_text(f"{key} {value}\n")
    evidence = snapshot(os.getpid(), args.region)
    if args.protect and not evidence["lowChainVerified"]:
        raise ValueError("Protection is blocked by an ancestor; game was not started")
    try:
        previous = json.loads(read(args.output) or "{}")
    except ValueError:
        previous = {}
    if isinstance(previous, dict) and previous.get("key") == args.key:
        # The wrapper's identity lets read-time recovery tell a killed wrapper apart.
        evidence.update({name: previous[name] for name in ("wrapperPid", "wrapperStartTicks") if name in previous})
    evidence.update(key=args.key, createdAt=datetime.now(timezone.utc).isoformat(),
                    status="protected_scope_started" if args.protect else "observing")
    atomic(args.output, evidence)
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    os.execvpe(argv[0], argv, os.environ)


def setup(restore=False):
    if os.getuid() != 0 or not os.environ.get("PKEXEC_UID", "").isdigit():
        raise ValueError("Use standard Polkit authorization (pkexec)")
    uid = int(os.environ["PKEXEC_UID"])
    if uid == 0:
        raise ValueError("A non-root login user is required")
    if not restore and Path(__file__).resolve() != HELPER:
        HELPER.parent.mkdir(parents=True, exist_ok=True)
        HELPER.write_bytes(Path(__file__).read_bytes()); HELPER.chmod(0o755)
    if restore:
        state = json.loads(RECORD.read_text())
        if state["uid"] != uid:
            raise ValueError("Setup belongs to another user")
        if any(CG.rglob("app-gameopti-*.scope")):
            raise ValueError("Close all GameOpti protected sessions before system restore")
        if (unit_state("plasma-foreground-booster.service", uid)["active"] == "active"
                and state.get("plasmaBeforeActive", "inactive") != "active"):
            raise ValueError("An independent Plasma booster now uses this setup; keep its system services enabled")
        if not state.get("written"):
            capacity = pairs(CG / "dmem.capacity")
            state["written"] = {str(p.relative_to(CG)): {k: v for k, v in pairs(p).items() if capacity.get(k) == v}
                                for p in CG.rglob("dmem.low")}
        # Restore only service states changed by this setup. Leave packages installed.
        for unit, before, user in ((USER_UNIT, state["user"], uid), (SYSTEM_UNIT, state["system"], None)):
            run = (lambda a: user_command(user, a)) if user is not None else command
            prefix = ["systemctl", *(["--user"] if user is not None else [])]
            if before["active"] != "active":
                run([*prefix, "stop", unit])
            if before["enabled"] not in {"enabled", "enabled-runtime"}:
                run([*prefix, "disable", unit])
        # Controller propagation is safe to leave available. It grants no per-game
        # protection once scopes exit; do not dismantle cgroups owned by systemd.
        for name, old in state.get("limits", {}).items():
            path = CG / name
            now = pairs(path)
            written = state.get("written", {}).get(name, {})
            for region, value in written.items():
                if now.get(region) == value:
                    path.write_text(f"{region} {old.get(region, 0)}\n")
        RECORD.unlink()
        return {"restored": True, "packagesKept": True, "controllerPropagationKept": True}
    if RECORD.exists():
        state = json.loads(RECORD.read_text())
        if state.get("phase") == "complete" and state.get("uid") == uid:
            repaired = prepare_ancestors(uid, state)
            atomic(RECORD, state)
            return {"alreadyPrepared": True, "repairedAncestors": repaired}
        raise ValueError("An incomplete setup exists; restore it before retrying")
    if "dmem" not in read(CG / "cgroup.controllers").split() or not pairs(CG / "dmem.capacity"):
        raise ValueError("Running kernel/driver does not expose a dmem memory region")
    state = {"uid": uid, "phase": "preparing", "system": unit_state(SYSTEM_UNIT), "user": unit_state(USER_UNIT, uid),
             "plasmaBeforeActive": unit_state("plasma-foreground-booster.service", uid)["active"],
             "globalUserEnabled": command(["systemctl", "--global", "is-enabled", USER_UNIT]).stdout.strip(),
             "packageExisted": bool(shutil.which("dmemcg-booster")), "limits": {}, "written": {}}
    if "masked" in {state["system"]["enabled"], state["user"]["enabled"]}:
        raise ValueError("A booster is masked by the administrator; unmask it explicitly first")
    for path in CG.rglob("dmem.low"):
        state["limits"][str(path.relative_to(CG))] = pairs(path)
    atomic(RECORD, state)  # recovery record precedes package hooks / service changes
    if not state["packageExisted"]:
        if not shutil.which("pacman") or "cachyos" not in read("/etc/os-release").lower():
            raise ValueError("Install your distribution's dmemcg-booster package, then retry setup")
        # Use existing synchronized, signed repositories; never -Sy/-Syu or a kernel upgrade.
        config = command(["pacman-conf"], check=True).stdout
        sections = re.split(r"(?m)(?=^\[)", config)
        config = "".join(part for part in sections if not part.startswith("[") or part.startswith("[options]")
                         or (Path("/var/lib/pacman/sync") / (part.split("]", 1)[0][1:] + ".db")).exists())
        conf = RECORD.with_name("gpu-memory-pacman.conf")
        conf.write_text(config); conf.chmod(0o600)
        try:
            command(["pacman", "--config", str(conf), "-S", "--needed", "--noconfirm", "dmemcg-booster"], check=True, timeout=180)
        finally:
            conf.unlink(missing_ok=True)
        # Package hook enables ALL users. GameOpti prepares only the invoking user.
        if state["globalUserEnabled"] not in {"enabled", "enabled-runtime"}:
            command(["systemctl", "--global", "disable", USER_UNIT], check=True)
    command(["systemctl", "enable", "--now", SYSTEM_UNIT], check=True)
    result = user_command(uid, ["systemctl", "--user", "enable", "--now", USER_UNIT])
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Could not start the user booster")
    time.sleep(1)
    prepare_ancestors(uid, state)
    for path in CG.rglob("dmem.low"):
        name = str(path.relative_to(CG))
        state["limits"].setdefault(name, {})
        state["written"][name] = pairs(path)
    state["phase"] = "complete"
    atomic(RECORD, state)
    HELPER.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).read_bytes()
    if Path(__file__).resolve() != HELPER:
        HELPER.write_bytes(source); HELPER.chmod(0o755)
    return {"prepared": True, "uid": uid}


def prepare_ancestors(uid, state):
    """Repair the booster's retained-controller restart case, without replacing
    nonzero user protections. This is setup, never a periodic policy writer.
    """
    capacity = pairs(CG / "dmem.capacity")
    manager = f"user.slice/user-{uid}.slice/user@{uid}.service"
    paths = ["user.slice", f"user.slice/user-{uid}.slice", manager, manager + "/app.slice"]
    changed = []
    for group in paths:
        path = CG / group / "dmem.low"
        if not path.is_file():
            raise ValueError(f"dmem propagation is missing at {group}; inspect the packaged booster")
        name = str(path.relative_to(CG))
        original = pairs(path)
        state["limits"].setdefault(name, original)
        for region, value in capacity.items():
            if original.get(region) == 0:
                path.write_text(f"{region} {value}\n")
                changed.append(group + ":" + region)
        state["written"][name] = pairs(path)
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["probe", "snapshot", "run", "enter", "setup", "restore-system", "reconcile"])
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--key", default="")
    parser.add_argument("--region", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--metrics", default="")
    parser.add_argument("--target-executable", default="")
    parser.add_argument("--protect", type=int, choices=[0, 1], default=1)
    args, argv = parser.parse_known_args()
    args.command = argv
    if args.action in {"run", "enter"}:
        if not re.fullmatch(r"[A-Za-z0-9_.%+-]{1,200}", args.key) or not Path(args.output).is_absolute():
            raise ValueError("Invalid profile key/output")
        return run_scope(args) if args.action == "run" else enter(args)
    if argv:
        raise ValueError("Unexpected arguments")
    if args.action == "reconcile":
        if not re.fullmatch(r"[A-Za-z0-9_.%+-]{1,200}", args.key) or not Path(args.output).is_absolute() \
                or Path(args.output).name != "gpu-memory-session.json":
            raise ValueError("Invalid profile key/output")
        print(json.dumps(reconcile_session(args.output, args.key)))
        return 0
    value = probe() if args.action == "probe" else snapshot(args.pid, args.region) if args.action == "snapshot" else setup(args.action == "restore-system")
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error), "detail": getattr(error, "stderr", "")}))
        sys.exit(1)
