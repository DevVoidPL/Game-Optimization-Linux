"""Host bridge (stdlib only). Games and telemetry always run as the login user.

The only privileged verbs prepare/restore Valve's packaged dmemcg-booster.
No custom scheduler, kernel, VRAM ceiling, driver option or global game env.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

def smart_policy():
    # Privileged setup/restore must never import a user-writable policy module.
    if os.getuid() == 0:
        raise ValueError("Smart GPU Scope runs only as the login user")
    try:
        from . import smart_gpu_scope
    except ImportError:
        import smart_gpu_scope
    return smart_gpu_scope

CG = Path("/sys/fs/cgroup")
PROC = Path("/proc")
SYSTEM_UNIT = "dmemcg-booster-system.service"
USER_UNIT = "dmemcg-booster-user.service"
HELPER = Path("/usr/libexec/game-optimization-linux-gpu-memory")
RECORD = Path("/var/lib/game-optimization-linux/gpu-memory-setup.json")
PROTOCOL_VERSION = 3


def legacy_review():
    """Read-only snapshot. Matching legacy values are evidence, NOT ownership."""
    uid = int(os.environ.get("PKEXEC_UID", os.getuid()))
    raw = RECORD.read_bytes()
    state = json.loads(raw)
    blockers, changes, services, observations = [], [], [], []
    if not isinstance(state, dict):
        raise ValueError('Legacy journal must be a JSON object; preserve it for administrator review')
    for field in ('system', 'user', 'limits', 'written'):
        if not isinstance(state.get(field), dict):
            blockers.append(f'Missing or malformed legacy {field} mapping; cannot establish restore evidence')
            state[field] = {}
    if state.get("version") == 2 or state.get("uid") != uid or state.get("phase") != "complete":
        blockers.append("Expected a complete legacy Setup belonging to the login user")
    manager = f"user.slice/user-{uid}.slice/user@{uid}.service"
    allowed = {"user.slice/dmem.low", f"user.slice/user-{uid}.slice/dmem.low",
               manager + "/dmem.low", manager + "/app.slice/dmem.low"}
    if active_game_scopes():
        blockers.append("Close all GameOpti game scopes before reconciliation")
    blockers.extend(game_process_blockers(uid))
    if state.get("plasmaBeforeActive") != "inactive":
        blockers.append("Missing evidence that Plasma foreground booster was inactive before legacy Setup")
    plasma = unit_state("plasma-foreground-booster.service", uid)
    if plasma["active"] not in {"inactive", "failed"}:
        blockers.append("Independent Plasma booster is active or its state is unknown; no services will be stopped")
    for unit, key, user in ((SYSTEM_UNIT, "system", None), (USER_UNIT, "user", uid)):
        before, current = state.get(key, {}), unit_state(unit, user)
        if before.get("active") != "inactive" or before.get("enabled") != "disabled":
            blockers.append(f"{unit}: legacy service ownership is ambiguous; manual review required")
        if current["active"] not in {"active", "inactive", "failed"} or current["enabled"] not in {"enabled", "disabled"}:
            blockers.append(f"{unit}: unsupported current service state")
        services.append({"unit": unit, "user": user, "before": before, "current": current})
    written = state.get("written", {})
    if not written:
        blockers.append("No recorded writes; cannot infer ownership from current capacity")
    for name, regions in written.items():
        if not isinstance(regions, dict) or not isinstance(state['limits'].get(name, {}), dict):
            blockers.append(f'Malformed region evidence at {name}; before/after mappings required')
            continue
        # Historical journals snapshot every cgroup, not just Setup writes.
        # Equal before/after entries are observations and must never be restored.
        if regions == state.get("limits", {}).get(name):
            continue
        if (not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or
                Path(name).name != 'dmem.low' or (CG / name).is_symlink() or
                not (CG / name).resolve().is_relative_to(CG.resolve())):
            blockers.append(f"Unsafe legacy observation path: {name}")
            continue
        if name not in allowed:
            # Legacy Setup took a full-tree before/after snapshot around service
            # startup. A difference here is NOT proof of a GameOpti direct write.
            observations.append({"path": name, "before": state.get("limits", {}).get(name),
                                 "recorded": regions, "disposition": "preserved; not a proven Setup write"})
            continue
        path = CG / name
        current = pairs(path)
        for region, after in regions.items():
            before = state.get("limits", {}).get(name, {}).get(region)
            if before == after:
                continue
            now = current.get(region)
            if before != 0 or not isinstance(after, int) or isinstance(after, bool) or after <= 0 or now not in {before, after}:
                blockers.append(f"Conflicting or unproven value: {name} {region} (before={before}, recorded={after}, current={now})")
                continue
            info = path.stat()
            changes.append({"path": name, "region": region, "before": before, "after": after,
                            "current": now, "identity": [info.st_dev, info.st_ino]})
    snapshot = {"journalSha256": hashlib.sha256(raw).hexdigest(), "uid": uid,
                "bootId": read("/proc/sys/kernel/random/boot_id"), "services": services,
                "plasma": plasma, "changes": changes, "blockers": blockers,
                "preservedObservations": observations}
    token = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return {**snapshot, "token": token, "requiresAttestation": True,
            "warning": "Legacy data cannot prove current ownership. Approve ONLY if these two boosters and listed parent values were enabled solely by GameOpti and no other manager now depends on them. Otherwise cancel and review with the administrator."}


def reconcile_legacy(token, approved):
    """One-time explicitly attested conversion, then normal ownership rollback."""
    authorize_setup()
    if approved is not True:
        raise ValueError("Explicit ownership attestation and Polkit authorization are required")
    init_coordinator()
    with coordinator_lock(), coordinator_lock("sessions.lock", wait=False):
        plan = legacy_review()
        if plan["token"] != token or plan["blockers"]:
            raise ValueError("Legacy review changed or is blocked; refresh and review again: " + "; ".join(plan["blockers"]))
        raw = RECORD.read_bytes()
        backup = RECORD.parent / ("legacy-setup-" + plan["journalSha256"] + ".json")
        if backup.exists():
            if backup.is_symlink() or backup.read_bytes() != raw:
                raise ValueError("Original backup conflicts; administrator review required")
        else:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        # Root-owned evidence + original global state must precede any host changes.
        atomic(backup.with_suffix(".review.json"), {"plan": plan, "globalBefore": global_status(),
               "approval": "login user attested exclusive GameOpti ownership via Polkit"})
        state = {"version": 2, "uid": plan["uid"], "bootId": plan["bootId"], "phase": "complete",
                 "legacyBackup": str(backup), "plasmaBeforeActive": "inactive", "services": [], "changes": []}
        for service in plan["services"]:
            current = service["current"]
            for action, needed in (("enable", current["enabled"] == "enabled"), ("start", current["active"] == "active")):
                if needed:
                    state["services"].append({"unit": service["unit"], "user": service["user"],
                        "action": action, "status": "applied", "before":
                        {**service["before"], **({"enabled": current["enabled"]} if action == "start" else {})},
                        "invocation": current.get("invocation", "")})
        for change in plan["changes"]:
            if change["current"] == change["after"]:
                state["changes"].append({**change, "status": "applied"})
        atomic(RECORD, state)
        save_global({"enabled": False, "cleanupState": "restoring", "baselineReady": False,
                     "legacyBackup": str(backup)})
        # _cleanup_global would reacquire sessions.lock. Keep it held here instead.
        try:
            result = restore_setup(state)
            preserved = bool(result.get("skippedChanges") or result.get("skippedServices"))
            remaining = any(unit_state(s["unit"], s["user"])["active"] == "active" or
                            unit_state(s["unit"], s["user"])["enabled"] == "enabled" for s in plan["services"])
            cleanup = "external_changes_preserved" if preserved or remaining else "complete"
            value = {"enabled": False, "cleanupState": cleanup, "baselineReady": False,
                     "legacyBackup": str(backup), "cleanupResult": result, "externalBoostersActive": remaining}
            save_global(value)
            return value
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            save_global({"enabled": False, "cleanupState": "failed", "baselineReady": False,
                         "legacyBackup": str(backup), "error": str(error)})
            raise


def component_status():
    """Host-side comparison: sandbox /usr/libexec is not the host filesystem."""
    source = Path(__file__).read_bytes()
    installed = HELPER.read_bytes() if HELPER.is_file() and not HELPER.is_symlink() else b""
    return {"protocol": PROTOCOL_VERSION, "rootHelper": "current" if source == installed else
            "outdated" if installed else "missing", "rootHelperPath": str(HELPER)}


def update_helper():
    authorize_setup()
    init_coordinator()
    with coordinator_lock(), coordinator_lock("sessions.lock", wait=False):
        if active_game_scopes():
            raise ValueError("Close all game sessions before updating the host helper")
        if HELPER.is_symlink():
            raise ValueError("Refusing a symlink host helper")
        HELPER.parent.mkdir(parents=True, exist_ok=True)
        if HELPER.exists():
            old = HELPER.read_bytes()
            backup = RECORD.parent / ("host-helper-" + hashlib.sha256(old).hexdigest() + ".py.backup")
            if not backup.exists():
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(old); stream.flush(); os.fsync(stream.fileno())
        temporary = HELPER.with_name(HELPER.name + ".update")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
        with os.fdopen(fd, "wb") as stream:
            stream.write(Path(__file__).read_bytes()); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(HELPER)
    return component_status()


def coordinator_path(name):
    return RECORD.parent / ("gpu-memory-" + name)


def init_coordinator():
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    for name in ("gate.lock", "sessions.lock", "worker.lock"):
        path = coordinator_path(name)
        fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW, 0o644)
        os.fchmod(fd, 0o644)
        os.close(fd)


@contextmanager
def coordinator_lock(name="gate.lock", exclusive=True, wait=True):
    with coordinator_path(name).open("r") as handle:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) |
                    (0 if wait else fcntl.LOCK_NB))
        yield handle


def global_status():
    try:
        state = json.loads(coordinator_path("global.json").read_text())
        if not isinstance(state.get("enabled"), bool):
            raise ValueError("Invalid global GPU-memory state")
        return state
    except FileNotFoundError:
        return {"enabled": True, "cleanupState": "not_requested", "baselineReady": False}
    except (OSError, ValueError) as error:
        return {"enabled": False, "cleanupState": "uncertain", "baselineReady": False, "error": str(error)}


def save_global(state):
    atomic(coordinator_path("global.json"), state)
    coordinator_path("global.json").chmod(0o644)  # launchers read; only root writes


def admit_optimization(requested):
    if not requested:
        return None
    if not coordinator_path("gate.lock").exists():
        # Old independently prepared installations still work; enter() rechecks OFF.
        return None
    with coordinator_lock():
        if not global_status()["enabled"]:
            return None
        handle = coordinator_path("sessions.lock").open("r")
        fcntl.flock(handle, fcntl.LOCK_SH)
        return handle


def active_game_scopes():
    # Includes older wrappers, other users and descendants left after wrapper death.
    return any(pairs(path / "cgroup.events").get("populated") != 0
               for path in CG.rglob("app-gameopti-*.scope"))


def game_process_blockers(uid):
    """Conservative read-only Wine/Proton check, including shared Flatpak cgroups.

    Never use dmem.low == 0 as evidence that no game is running. Native games
    outside GameOpti scopes cannot be identified reliably; confirmation still
    requires the user to close ALL games.
    """
    blockers = []
    for process in PROC.glob('[0-9]*'):
        try:
            status = (process / 'status').read_text()
            owner = re.search(r'(?m)^Uid:\s+(\d+)', status)
            if not owner or int(owner[1]) != uid:
                continue
            comm = (process / 'comm').read_text().strip()
            argv = (process / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if re.search(r'(?i)(?:\.exe(?:\s|$)|\b(?:wineserver|wine64|wine-preloader|wine64-preloader|proton)(?:\s|$))', comm + ' ' + argv):
                group = (process / 'cgroup').read_text().strip()
                blockers.append(f"Close Wine/Proton game or launcher PID {process.name} ({comm}), cgroup {group}; shared cgroups are not safe recovery targets")
        except FileNotFoundError:
            continue  # process exited while reading
        except (OSError, ValueError) as error:
            blockers.append(f"Cannot exclude an active game at PID {process.name}: {error}")
    return blockers


def validate_restore_environment(state):
    """Called immediately before each systemctl mutation and each dmem write."""
    blockers = game_process_blockers(state['uid'])
    if active_game_scopes():
        blockers.append('Close all GameOpti game scopes before restoring')
    plasma = unit_state('plasma-foreground-booster.service', state['uid'])
    if plasma['active'] not in {'inactive', 'failed'}:
        blockers.append('Independent Plasma booster became active or unreadable; restore paused')
    if blockers:
        raise ValueError('; '.join(blockers))


def _cleanup_global():
    state = global_status()
    if state["enabled"]:
        return state
    try:
        with coordinator_lock("sessions.lock", wait=False):
            if active_game_scopes():
                state.update(cleanupState="waiting_for_sessions", baselineReady=False)
            elif RECORD.exists():
                journal = json.loads(RECORD.read_text())
                if journal.get("uid") != int(os.environ["PKEXEC_UID"]):
                    raise ValueError("Setup belongs to another user")
                if (unit_state("plasma-foreground-booster.service", journal["uid"])["active"] == "active"
                        and journal.get("plasmaBeforeActive", "inactive") != "active"):
                    raise ValueError("An independent Plasma booster now uses this setup")
                state.update(cleanupState="restoring", baselineReady=False)
                save_global(state)
                result = restore_setup(journal)
                preserved = bool(result.get("skippedChanges") or result.get("skippedServices"))
                state.update(cleanupState="external_changes_preserved" if preserved else "complete",
                             baselineReady=not preserved, cleanupResult=result, error="")
                external = any(unit_state(unit, user)["active"] == "active" for unit, user in (
                    (SYSTEM_UNIT, None), (USER_UNIT, journal["uid"]),
                    ("plasma-foreground-booster.service", journal["uid"])))
                state["externalBoostersActive"] = external
                state["baselineReady"] = False  # OFF alone cannot certify a measurement baseline
            elif state.get("cleanupState") == "restoring":
                state.update(cleanupState="uncertain", baselineReady=False,
                             error="Interrupted cleanup lost its final result; review required")
            elif state.get("cleanupState") not in {"external_changes_preserved", "failed", "uncertain"}:
                state.update(cleanupState="complete", baselineReady=False, error="")
    except BlockingIOError:
        state.update(cleanupState="waiting_for_sessions", baselineReady=False)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        state.update(cleanupState="failed", baselineReady=False, error=str(error))
        save_global(state)
        raise ValueError(str(error)) from error
    save_global(state)
    return state


def cleanup_global():
    authorize_setup()
    init_coordinator()
    with coordinator_lock():
        return _cleanup_global()


def start_cleanup_worker():
    # Copy reviewed code to root-owned helper before scheduling privileged recovery.
    HELPER.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != HELPER:
        temporary = HELPER.with_suffix(".tmp")
        temporary.write_bytes(Path(__file__).read_bytes())
        temporary.chmod(0o755)
        temporary.replace(HELPER)
    subprocess.Popen([sys.executable, str(HELPER), "cleanup-worker"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def cleanup_worker():
    authorize_setup()
    init_coordinator()
    try:
        with coordinator_lock("worker.lock", wait=False):
            while cleanup_global().get("cleanupState") == "waiting_for_sessions":
                time.sleep(1)
    except BlockingIOError:
        pass  # another worker owns recovery


def set_global(enabled):
    authorize_setup()
    init_coordinator()
    with coordinator_lock():
        state = global_status()
        if enabled:
            _setup()
            state = {"enabled": True, "cleanupState": "not_requested", "baselineReady": False}
            save_global(state)
        else:
            state.update(enabled=False, baselineReady=False)
            if state.get("cleanupState") not in {"restoring", "failed", "uncertain", "external_changes_preserved"}:
                state["cleanupState"] = "requested"
            save_global(state)  # OFF is durable even if cleanup/authorization later fails
            state = _cleanup_global()
            if state["cleanupState"] == "waiting_for_sessions":
                try:
                    start_cleanup_worker()
                except OSError as error:
                    state.update(cleanupState="failed", error=str(error))
                    save_global(state)
                    raise
        return state


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
            "enabled": run([*argv, "is-enabled", unit]).stdout.strip(),
            "invocation": run([*argv, "show", "--property=InvocationID", "--value", unit]).stdout.strip()}


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
    journal_state = "absent"
    if RECORD.exists():
        try:
            journal_state = "current" if json.loads(RECORD.read_text()).get("version") == 2 else "legacy"
        except (OSError, ValueError):
            # Journals are deliberately mode 0600. A login-user probe must not
            # fail or pretend to know their schema; explicit Polkit review reads it.
            journal_state = "review_required"
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
            "resourceControlAvailable": bool(uid and smart_policy().available() and manager),
            "controllers": read(CG / "cgroup.controllers").split(), "managerCgroup": manager,
            "readyRegions": ready_regions,
            "delegated": "dmem" in read(CG / manager.lstrip("/") / "cgroup.controllers").split(),
            "writable": os.access(CG / manager.lstrip("/") / "app.slice", os.W_OK),
            "systemBooster": unit_state(SYSTEM_UNIT), "userBooster": unit_state(USER_UNIT, uid),
            "plasmaBooster": unit_state("plasma-foreground-booster.service", uid),
            "boosterInstalled": bool(shutil.which("dmemcg-booster")),
            "boosterPackage": command(["pacman", "-Q", "dmemcg-booster"]).stdout.strip() if shutil.which("pacman") else "",
            "helperInstalled": HELPER.exists(), "managedSetup": RECORD.exists(),
            "components": component_status(),
            "legacySetup": journal_state == "legacy", "setupJournalState": journal_state,
            "globalGpuMemory": global_status(),
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
    with tmp.open("w") as stream:
        tmp.chmod(0o600)
        stream.write(json.dumps(value, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


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


def smart_status(args):
    """Revalidate now; never present a saved green snapshot as current state."""
    view = reconcile_session(args.output, args.key)
    record = view.get("record", {})
    if record.get("status") in TERMINAL_SESSION:
        result = {"state": "finished", "protectionVerified": False}
        try:
            resource = json.loads(read(Path(args.output).with_name('smart-resource-status.json')) or '{}')
            if resource.get('state') == 'finished':
                result['resourceController'] = resource
        except (ValueError, TypeError):
            pass
        return result
    if view.get("state") != "running":
        return {"state": "active_unverified", "protectionVerified": False}
    group = record.get("cgroup", "")
    policy = smart_policy()
    observer = policy.ScopeObserver(group, args.region, record.get("chain", []),
                             requested=bool(record.get("protected")))
    result = observer.update(policy.scope_samples(sys.modules[__name__], group, args.region),
                             region_available=args.region in pairs(CG / "dmem.capacity"))
    saved = record.get("smartScope", {})
    try:
        resource = json.loads(read(Path(args.output).with_name('smart-resource-status.json')) or '{}')
        if 0 <= time.time() - float(resource.get('observedAt', 0)) <= 10:
            result['resourceController'] = resource
    except (ValueError, TypeError):
        pass
    result["transitions"] = saved.get("transitions", [])[-32:]
    result["managers"] = saved.get("managers", [])
    window = saved.get("compositorObservation", {})
    if 0 <= time.time() - float(window.get("observedAt", 0)) <= 5:
        result["compositorObservation"] = window
    return result


def smart_tick(args, observer, managers):
    record = json.loads(read(args.output) or "{}")
    group = record.get("cgroup", "")
    if not group:
        return observer
    policy = smart_policy()
    if observer is None:
        observer = policy.ScopeObserver(group, args.region, record.get("chain", []), requested=bool(args.protect))
    samples = policy.scope_samples(sys.modules[__name__], group, args.region)
    names = {sample.get("command", "") for sample in samples}
    current_managers = set(managers)
    if "gamescope" in names:
        current_managers.add("gamescope")
    if "aqueous" in names:
        current_managers.add("aqueous (compositor only; no dmem API)")
    value = observer.update(samples, managers=current_managers,
                            region_available=args.region in pairs(CG / "dmem.capacity"))
    try:
        window = json.loads(read(Path(args.output).with_name("scope-window.json")))
        if 0 <= time.time() - float(window.get("observedAt", 0)) <= 5:
            value["compositorObservation"] = window
    except (ValueError, TypeError):
        pass
    atomic(args.output, {**record, "smartScope": value})
    return observer


def run_scope(args):
    if os.getuid() == 0:
        raise ValueError("The game must run unprivileged")
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not argv:
        raise ValueError("Missing game command")
    # Aqueous must not share the resource-controlled game scope. Delegate the
    # same wrapper to its inner game command, leaving the nested compositor in
    # the caller's original cgroup. No PID/window guessing or process migration.
    if getattr(args, 'smart_scope', False) and '--' in argv:
        split = argv.index('--')
        if any(value == 'game_optimization_linux.services.aqueous' or
               Path(value).name == 'aqueous-host.py' for value in argv[:split]):
            inner = [sys.executable, str(Path(__file__).resolve()), 'run', '--key', args.key,
                '--region', args.region, '--output', args.output, '--protect', str(args.protect), '--smart-scope']
            if args.metrics:
                inner += ['--metrics', args.metrics]
            if args.target_executable:
                inner += ['--target-executable', args.target_executable]
            return subprocess.call([*argv[:split + 1], *inner, '--', *argv[split + 1:]])
    requested = bool(args.protect or os.environ.get("GAMEOPTI_GPU_MEMORY_VKD3D_ORIGINAL") or
                     any(value.startswith("GAMEOPTI_GPU_MEMORY_VKD3D_ORIGINAL=") for value in argv))
    lease = admit_optimization(requested)
    if not global_status()["enabled"]:
        args.protect = 0
        restore_allocator_environment(os.environ)
        argv = restore_allocator_command(argv)
        if not getattr(args, "smart_scope", False) and not args.metrics:
            return os.execvpe(argv[0], argv, os.environ)
    unit = "app-gameopti-" + hashlib.sha256(args.key.encode()).hexdigest()[:12] + "-" + os.urandom(5).hex() + ".scope"
    # systemd owns lifetime and descendants. Create the scope before Vulkan/Wine allocation.
    child = [sys.executable, str(Path(__file__).resolve()), "enter", "--key", args.key,
             "--region", args.region, "--output", args.output, "--protect", str(args.protect), "--", *argv]
    scoped = bool(args.protect or requested or (shutil.which("systemd-run") and Path("/run/systemd/system").exists()))
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
    smart = None
    resource_started = False
    next_smart = 0
    managers = []
    if getattr(args, "smart_scope", False):
        try:
            if unit_state(USER_UNIT, os.getuid())["active"] == "active":
                managers.append(USER_UNIT)
        except (OSError, subprocess.SubprocessError):
            pass  # unavailable manager evidence is not an ownership claim
    try:
        while process.poll() is None or (scoped and scope_populated(args.output, unit)):
            waited_for_descendants |= process.poll() is not None
            if getattr(args, "smart_scope", False):
                try:
                    if time.monotonic() >= next_smart:
                        smart = smart_tick(args, smart, managers)
                        next_smart = time.monotonic() + 3
                    record = json.loads(read(args.output) or '{}')
                    if not resource_started and record.get('cgroup'):
                        smart_policy().start_controller(record['cgroup'],
                            Path(args.output).with_name('gpu-memory.json'),
                            Path(args.output).with_name('smart-resource-status.json'))
                        resource_started = True
                except (OSError, ValueError) as error:
                    if str(error) not in telemetry_errors:
                        telemetry_errors.append(str(error))
                    resource_started = True  # do not retry a failed service launch every tick
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
        if smart is not None:
            record = json.loads(read(args.output) or "{}")
            atomic(args.output, {**record, "smartScope": smart.finish(result)})
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
        if lease is not None:
            lease.close()


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
    # Hold admission through the actual dmem write/exec. OFF cannot pass the gate
    # while an admitted child is still applying its initial protection.
    if coordinator_path("gate.lock").exists():
        with coordinator_lock():
            return _enter(args)
    return _enter(args)


def _enter(args):
    if os.getuid() == 0:
        raise ValueError("The game must run unprivileged")
    # Caller owns the gate; taking it again would deadlock on another open fd.
    lease = None
    if global_status()["enabled"] and coordinator_path("sessions.lock").exists():
        lease = coordinator_path("sessions.lock").open("r")
        fcntl.flock(lease, fcntl.LOCK_SH)
    if not global_status()["enabled"]:
        args.protect = 0
        restore_allocator_environment(os.environ)
    if lease is not None:
        os.set_inheritable(lease.fileno(), True)
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
    evidence.update(key=args.key, protected=bool(args.protect), createdAt=datetime.now(timezone.utc).isoformat(),
                    status="protected_scope_started" if args.protect else "observing")
    atomic(args.output, evidence)
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not global_status()["enabled"]:
        argv = restore_allocator_command(argv)
    os.execvpe(argv[0], argv, os.environ)


def restore_allocator_environment(environment):
    original = environment.pop("GAMEOPTI_GPU_MEMORY_VKD3D_ORIGINAL", None)
    if original is not None:
        value = json.loads(original)
        if value is None:
            environment.pop("VKD3D_CONFIG", None)
        else:
            environment["VKD3D_CONFIG"] = value


def restore_allocator_command(argv):
    markers = [item.partition("=")[2] for item in argv
               if item.startswith("GAMEOPTI_GPU_MEMORY_VKD3D_ORIGINAL=")]
    if not markers:
        return argv
    original = json.loads(markers[-1])
    result = []
    for item in argv:
        if item.startswith("GAMEOPTI_GPU_MEMORY_VKD3D_ORIGINAL="):
            continue
        if item.startswith("VKD3D_CONFIG="):
            if original is not None:
                result.append("VKD3D_CONFIG=" + original)
        else:
            result.append(item)
    return result


def setup_service(state, unit, user, action):
    """Journal intent before systemctl; an interrupted command is not ownership proof."""
    operation = {"unit": unit, "user": user, "action": action, "status": "pending",
                 "before": unit_state(unit, user)}
    state["services"].append(operation)
    atomic(RECORD, state)
    run = (lambda argv: user_command(user, argv)) if user is not None else command
    result = run(["systemctl", *(["--user"] if user is not None else []), action, unit])
    if result.returncode:
        # A failed command may still have changed systemd state. Do not infer ownership.
        if unit_state(unit, user) == operation["before"]:
            operation["status"] = "failed"
        atomic(RECORD, state)
        raise ValueError(result.stderr.strip() or f"Could not {action} {unit}")
    operation["status"] = "applied"
    if action == "start":
        operation["invocation"] = unit_state(unit, user).get("invocation", "")
    atomic(RECORD, state)


def restore_setup(state):
    """Rollback known operations; retain progress and ambiguity for a safe retry."""
    if state.get("version") != 2:
        raise ValueError("Legacy setup journal does not prove ownership; automatic restore refused. "
                         "Keep the journal. Use Review legacy Setup (Polkit), close all games, inspect the plan, "
                         "then explicitly confirm exclusive ownership and Reconcile to Off. No host settings were changed")
    state["phase"] = "restoring"
    atomic(RECORD, state)
    errors = []
    preserved_units = {item["unit"] for item in state["services"] if item.get("preserveUnit")}
    # Stop writers before touching limits. Do not repeat successful partial rollback.
    for operation in reversed(state["services"]):
        if operation["status"] in {"restored", "skipped", "failed"}:
            continue
        if operation["status"] == "pending":
            errors.append(f"Uncertain service operation: {operation['unit']} {operation['action']}")
            break
        action = {"start": "stop", "enable": "disable"}[operation["action"]]
        user = operation["user"]
        run = (lambda argv: user_command(user, argv)) if user is not None else command
        try:
            current = unit_state(operation["unit"], user)
            field, expected = ("active", "active") if operation["action"] == "start" else ("enabled", "enabled")
            if (operation["unit"] in preserved_units or current[field] != expected or
                    (operation["action"] == "start" and
                     (read("/proc/sys/kernel/random/boot_id") != state["bootId"] or
                      current["enabled"] != operation["before"]["enabled"] or
                      current.get("invocation", "") != operation.get("invocation", "")))):
                operation["status"] = "skipped"
                if (operation["action"] == "start" and
                        read("/proc/sys/kernel/random/boot_id") == state["bootId"] and
                        current.get("invocation", "") != operation.get("invocation", "")):
                    preserved_units.add(operation["unit"])
                    operation["preserveUnit"] = True
                atomic(RECORD, state)
                continue
            validate_restore_environment(state)
            if unit_state(operation['unit'], user) != current:
                raise ValueError(f"Service changed before {action}: {operation['unit']}; refresh recovery")
            if action == 'disable' and current['active'] == 'active' and any(
                    item['unit'] == operation['unit'] and item['action'] == 'start' and
                    item['status'] == 'restored' for item in state['services']):
                raise ValueError(f"Independent writer restarted before disable: {operation['unit']}; service preserved")
            result = run(["systemctl", *(["--user"] if user is not None else []), action, operation["unit"]])
            if result.returncode:
                raise ValueError(result.stderr.strip() or f"Could not {action} {operation['unit']}")
            after = unit_state(operation["unit"], user)
            if after[field] == expected:
                raise ValueError(f"Service state did not change after {action} {operation['unit']}")
            operation["status"] = "restored"
            atomic(RECORD, state)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(str(error))
            break  # Do not disable a writer whose stop failed; retry from this point.
    if not errors:
        boot = read("/proc/sys/kernel/random/boot_id")
        for change in reversed(state["changes"]):
            if change["status"] in {"restored", "skipped", "failed"}:
                continue
            if change["status"] == "pending":
                errors.append(f"Uncertain dmem write: {change['path']}")
                continue
            if preserved_units:
                change["status"] = "skipped"
                atomic(RECORD, state)
                continue  # an externally restarted writer may still rely on these parents
            path = CG / change["path"]
            try:
                # Never replay a stale journal against a recreated cgroup or reboot.
                identity = list((path.stat().st_dev, path.stat().st_ino)) if path.exists() else None
                if (boot != state["bootId"] or identity != change["identity"] or
                        pairs(path).get(change["region"]) != change["after"]):
                    change["status"] = "skipped"
                else:
                    validate_restore_environment(state)
                    for service in state['services']:
                        if service['action'] == 'start' and service['status'] == 'restored':
                            current = unit_state(service['unit'], service['user'])
                            if current['active'] not in {'inactive', 'failed'}:
                                raise ValueError(f"Independent writer restarted: {service['unit']}; limits preserved, close other managers before retry")
                    latest = path.stat()
                    if ([latest.st_dev, latest.st_ino] != change['identity'] or
                            pairs(path).get(change['region']) != change['after']):
                        change['status'] = 'skipped'
                        atomic(RECORD, state)
                        continue
                    path.write_text(f"{change['region']} {change['before']}\n")
                    if pairs(path).get(change["region"]) != change["before"]:
                        raise ValueError(f"dmem rollback verification failed: {change['path']} {change['region']}")
                    change["status"] = "restored"
                atomic(RECORD, state)
            except (OSError, ValueError) as error:
                errors.append(str(error))
    if errors:
        state["rollbackErrors"] = errors
        atomic(RECORD, state)
        raise ValueError("Incomplete setup rollback; journal retained: " + "; ".join(errors))
    skipped = [item for item in state["changes"] if item["status"] == "skipped"]
    RECORD.unlink()
    return {"restored": True, "packagesKept": True, "controllerPropagationKept": True,
            "skippedChanges": skipped,
            "skippedServices": [item for item in state["services"] if item["status"] == "skipped"]}


def authorize_setup():
    if os.getuid() != 0 or not os.environ.get("PKEXEC_UID", "").isdigit():
        raise ValueError("Use standard Polkit authorization (pkexec)")
    uid = int(os.environ["PKEXEC_UID"])
    if uid == 0:
        raise ValueError("A non-root login user is required")
    return uid


def setup(restore=False):
    authorize_setup()
    init_coordinator()
    with coordinator_lock():
        if restore:
            # Explicit Restore also closes admission, including after failed cleanup.
            state = global_status()
            state.update(enabled=False, baselineReady=False)
            if state.get("cleanupState") not in {"restoring", "failed", "uncertain", "external_changes_preserved"}:
                state["cleanupState"] = "requested"
            save_global(state)
            result = _cleanup_global()
            if result["cleanupState"] == "waiting_for_sessions":
                start_cleanup_worker()
                return result
            return result.get("cleanupResult", {"restored": result["cleanupState"] == "complete"})
        result = _setup()
        save_global({"enabled": True, "cleanupState": "not_requested", "baselineReady": False})
        return result


def _setup(restore=False):
    uid = authorize_setup()
    if RECORD.exists():
        state = json.loads(RECORD.read_text())
        if state["uid"] != uid:
            raise ValueError("Setup belongs to another user")
        if state.get("version") != 2:
            raise ValueError("Legacy setup journal does not prove ownership; manual review required")
        if restore:
            if any(CG.rglob("app-gameopti-*.scope")):
                raise ValueError("Close all GameOpti protected sessions before system restore")
            if (unit_state("plasma-foreground-booster.service", uid)["active"] == "active"
                    and state.get("plasmaBeforeActive", "inactive") != "active"):
                raise ValueError("An independent Plasma booster now uses this setup; keep its system services enabled")
            return restore_setup(state)
        if state.get("phase") == "complete":
            # Idempotent: do not adopt or reapply changes made by another manager.
            return {"alreadyPrepared": True, "repairedAncestors": []}
        raise ValueError("An incomplete setup exists; restore it before retrying")
    if restore:
        raise ValueError("No GameOpti setup journal exists")
    if active_game_scopes():
        raise ValueError("Active game sessions prevent system setup")
    try:
        with coordinator_lock("sessions.lock", wait=False):
            pass
    except BlockingIOError as error:
        raise ValueError("Active game sessions prevent system setup") from error
    if "dmem" not in read(CG / "cgroup.controllers").split() or not pairs(CG / "dmem.capacity"):
        raise ValueError("Running kernel/driver does not expose a dmem memory region")
    # Package hooks have unbounded external side effects; require a preinstalled package.
    if not shutil.which("dmemcg-booster"):
        raise ValueError("Install your distribution's dmemcg-booster package, then retry setup")
    state = {"version": 2, "uid": uid, "phase": "preparing",
             "bootId": read("/proc/sys/kernel/random/boot_id"),
             "system": unit_state(SYSTEM_UNIT), "user": unit_state(USER_UNIT, uid),
             "plasmaBeforeActive": unit_state("plasma-foreground-booster.service", uid)["active"],
             "services": [], "changes": []}
    for before in (state["system"], state["user"]):
        if before["enabled"] not in {"enabled", "enabled-runtime", "disabled", "static", "indirect"} \
                or before["active"] not in {"active", "inactive", "failed"}:
            raise ValueError("Unsupported or masked booster state; inspect systemd services first")
    atomic(RECORD, state)
    try:
        for unit, before, user in ((SYSTEM_UNIT, state["system"], None), (USER_UNIT, state["user"], uid)):
            if before["enabled"] == "disabled":
                setup_service(state, unit, user, "enable")
            if before["active"] != "active":
                setup_service(state, unit, user, "start")
        time.sleep(1)
        prepare_ancestors(uid, state)
        state["phase"] = "complete"
        atomic(RECORD, state)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        try:
            restore_setup(state)
        except (OSError, ValueError, subprocess.SubprocessError) as rollback_error:
            raise ValueError(f"Setup failed: {error}; {rollback_error}") from error
        raise ValueError(f"Setup failed; rollback completed: {error}") from error
    return {"prepared": True, "uid": uid}


def prepare_ancestors(uid, state):
    """Record only successful, direct zero-to-capacity writes by this invocation."""
    capacity = pairs(CG / "dmem.capacity")
    manager = f"user.slice/user-{uid}.slice/user@{uid}.service"
    paths = ["user.slice", f"user.slice/user-{uid}.slice", manager, manager + "/app.slice"]
    changed = []
    for group in paths:
        path = CG / group / "dmem.low"
        if not path.is_file():
            raise ValueError(f"dmem propagation is missing at {group}; inspect the packaged booster")
        for region, value in capacity.items():
            if value <= 0 or pairs(path).get(region) != 0:
                continue
            change = {"path": str(path.relative_to(CG)), "region": region, "before": 0,
                      "after": value, "identity": [path.stat().st_dev, path.stat().st_ino], "status": "pending"}
            state["changes"].append(change)
            atomic(RECORD, state)
            try:
                # Recheck after journaling: another manager may have supplied protection.
                if pairs(path).get(region) != 0:
                    change["status"] = "skipped"
                else:
                    path.write_text(f"{region} {value}\n")
                    change["status"] = "applied"
                    changed.append(group + ":" + region)
            except OSError:
                # An exception need not mean the kernel applied no bytes.
                # Unknown outcomes stay pending, never become inferred ownership.
                if pairs(path).get(region) == change["before"]:
                    change["status"] = "failed"
                atomic(RECORD, state)
                raise
            atomic(RECORD, state)
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["probe", "snapshot", "run", "enter", "setup", "restore-system", "reconcile", "smart-status",
                                          "global-on", "global-off", "global-status", "cleanup", "cleanup-worker",
                                          "legacy-review", "legacy-reconcile", "components", "update-helper"])
    parser.add_argument("--review-token", default="")
    parser.add_argument("--approve-exclusive-ownership", action="store_true")
    parser.add_argument("--smart-scope", action="store_true")
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
    if args.action in {"legacy-review", "legacy-reconcile", "components", "update-helper"}:
        value = (legacy_review() if args.action == "legacy-review" else
                 reconcile_legacy(args.review_token, args.approve_exclusive_ownership) if args.action == "legacy-reconcile" else
                 update_helper() if args.action == "update-helper" else component_status())
        print(json.dumps(value))
        return 0
    if args.action in {"global-on", "global-off", "global-status", "cleanup", "cleanup-worker"}:
        value = (set_global(args.action == "global-on") if args.action in {"global-on", "global-off"}
                 else global_status() if args.action == "global-status"
                 else cleanup_global() if args.action == "cleanup" else cleanup_worker())
        print(json.dumps(value))
        return 0
    if args.action in {"reconcile", "smart-status"}:
        if not re.fullmatch(r"[A-Za-z0-9_.%+-]{1,200}", args.key) or not Path(args.output).is_absolute() \
                or Path(args.output).name != "gpu-memory-session.json":
            raise ValueError("Invalid profile key/output")
        print(json.dumps(smart_status(args) if args.action == "smart-status" else reconcile_session(args.output, args.key)))
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
