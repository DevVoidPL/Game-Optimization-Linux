"""GPU-memory session records left by an interrupted wrapper are reconciled at read time.

Synthetic records, a temporary cgroup tree and short disposable processes only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from game_optimization_linux.services import gpu_memory as gm
from game_optimization_linux.services import gpu_memory_host as host

KEY = "123"
SCOPE = "/user.slice/user-1000.slice/user@1000.service/app.slice/app-gameopti-abc123-def456.scope"
HOST = Path(host.__file__)


def _dead_pid() -> tuple[int, list[str]]:
    process = subprocess.Popen(["/bin/true"])
    ticks = host.start_ticks(process.pid)
    process.wait()
    return process.pid, ticks or ["1"]


def _record(tmp_path: Path, **values) -> Path:
    path = tmp_path / "gpu-memory-session.json"
    path.write_text(json.dumps({"key": KEY, "createdAt": "2026-10-27T10:00:00+00:00", **values}, indent=2) + "\n")
    return path


@pytest.fixture
def cg(tmp_path, monkeypatch):
    root = tmp_path / "cgroup"
    root.mkdir()
    monkeypatch.setattr(host, "CG", root)
    return root


def _scope(cg: Path, populated: int | None) -> Path:
    directory = cg / SCOPE.lstrip("/")
    directory.mkdir(parents=True, exist_ok=True)
    if populated is not None:
        (directory / "cgroup.events").write_text(f"populated {populated}\nfrozen 0\n")
    return directory


def _started(pid: int, ticks: list[str], **extra) -> dict:
    return {"status": "protected_scope_started", "pid": pid, "startTicks": ticks, "cgroup": SCOPE,
            "protectionVerified": True, "lowChainVerified": True, "chain": [{"path": SCOPE, "low": {"gpu/vram": 8}}],
            "wrapperPid": 0, "wrapperStartTicks": [], **extra}


def test_process_and_scope_gone_persists_interrupted_once_and_keeps_evidence(tmp_path, cg) -> None:
    pid, ticks = _dead_pid()
    path = _record(tmp_path, **_started(pid, ticks, wrapperSignal="SIGINT", reason="original diagnostic"))
    view = host.reconcile_session(path, KEY, now="2026-10-27T11:00:00+00:00")
    assert view["state"] == "interrupted" and view["persisted"] is True
    record = json.loads(path.read_text())
    assert record["status"] == "interrupted" and record["statusBeforeReconciliation"] == "protected_scope_started"
    assert record["reconciliation"]["reason"] == "wrapper_interrupted_after_launch"
    assert record["reconciliation"]["scope"] == "gone" and record["reconciliation"]["gameAlive"] is False
    assert record["protectionVerified"] is False and record["lastProtectionVerified"] is True
    assert record["chain"] == [{"path": SCOPE, "low": {"gpu/vram": 8}}] and record["reason"] == "original diagnostic"
    assert path.stat().st_mode & 0o777 == 0o600
    first = path.read_bytes()
    again = host.reconcile_session(path, KEY)  # idempotent
    assert again["state"] == "interrupted" and again["persisted"] is False and path.read_bytes() == first


def test_scope_populated_after_wrapper_exit_is_never_finalized(tmp_path, cg) -> None:
    pid, ticks = _dead_pid()  # the recorded process is gone, but a detached child keeps the scope busy
    _scope(cg, 1)
    path = _record(tmp_path, **_started(pid, ticks))
    before = path.read_bytes()
    assert host.reconcile_session(path, KEY)["state"] == "running" and path.read_bytes() == before
    _scope(cg, 0)  # the last process left; systemd has not collected the scope yet
    view = host.reconcile_session(path, KEY)
    assert view["state"] == "interrupted" and view["evidence"]["scope"] == "empty"
    assert json.loads(path.read_text())["reconciliation"]["reason"] == "wrapper_ended_without_final_state"


def test_a_still_running_matching_process_is_never_marked_exited(tmp_path, cg) -> None:
    path = _record(tmp_path, **_started(os.getpid(), host.start_ticks(os.getpid())))
    before = path.read_bytes()
    view = host.reconcile_session(path, KEY)
    assert view["state"] == "running" and view["evidence"]["gameAlive"] is True and path.read_bytes() == before


def test_pid_reuse_with_different_start_ticks_is_not_the_recorded_process(tmp_path, cg) -> None:
    path = _record(tmp_path, **_started(os.getpid(), ["1"]))  # a live PID, but not the recorded start
    view = host.reconcile_session(path, KEY)
    assert view["evidence"]["gameAlive"] is False and view["state"] == "interrupted"


def test_wrapper_killed_before_the_game_started(tmp_path, cg) -> None:
    wrapper, ticks = _dead_pid()
    path = _record(tmp_path, status="starting", protected=True, wrapperPid=wrapper, wrapperStartTicks=ticks)
    view = host.reconcile_session(path, KEY)
    assert view["state"] == "interrupted"
    assert json.loads(path.read_text())["reconciliation"]["reason"] == "launch_interrupted_before_game"
    # A wrapper that is still alive keeps the starting record as it is.
    alive = _record(tmp_path, status="starting", wrapperPid=os.getpid(), wrapperStartTicks=host.start_ticks(os.getpid()))
    assert host.reconcile_session(alive, KEY)["state"] == "running"


def test_missing_evidence_is_unverified_and_not_written(tmp_path, cg) -> None:
    pid, ticks = _dead_pid()
    _scope(cg, None)  # scope directory without cgroup.events
    path = _record(tmp_path, **_started(pid, ticks))
    before = path.read_bytes()
    assert host.reconcile_session(path, KEY)["state"] == "unverified" and path.read_bytes() == before
    old_starting = _record(tmp_path, status="starting")  # record from a build without the wrapper identity
    before = old_starting.read_bytes()
    assert host.reconcile_session(old_starting, KEY)["state"] == "unverified" and old_starting.read_bytes() == before
    no_ticks = _record(tmp_path, **_started(pid, []))
    assert host.reconcile_session(no_ticks, KEY)["state"] == "unverified"


@pytest.mark.parametrize("text, state", [("{not json", "malformed"), ('{"key": "999", "status": "observing"}', "foreign"),
                                         ('{"key": "123", "status": "weird"}', "malformed"), ('["list"]', "foreign")])
def test_malformed_or_foreign_record_is_never_overwritten(tmp_path, cg, text, state) -> None:
    path = tmp_path / "gpu-memory-session.json"
    path.write_text(text)
    assert host.reconcile_session(path, KEY)["state"] == state and path.read_text() == text


def test_normal_exit_record_is_left_unchanged(tmp_path, cg) -> None:
    path = _record(tmp_path, status="exited", exitCode=0, pid=1, startTicks=["1"])
    before = path.read_bytes()
    assert host.reconcile_session(path, KEY)["state"] == "exited" and path.read_bytes() == before


def test_atomic_write_failure_keeps_the_previous_record(tmp_path, cg) -> None:
    pid, ticks = _dead_pid()
    path = _record(tmp_path, **_started(pid, ticks))
    before = path.read_bytes()

    def failing(*_args):
        raise OSError("disk full")

    view = host.reconcile_session(path, KEY, replace=failing)
    assert view["state"] == "interrupted" and view["persisted"] is False and "disk full" in view["error"]
    assert path.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


def test_reconcile_action_reads_only_and_validates_its_output(tmp_path, cg, monkeypatch, capsys) -> None:
    pid, ticks = _dead_pid()
    path = _record(tmp_path, **_started(pid, ticks))
    monkeypatch.setattr(host, "snapshot", lambda *a: pytest.fail("no sampling"))
    monkeypatch.setattr(host.subprocess, "Popen", lambda *a, **k: pytest.fail("no scope or process is created"))
    monkeypatch.setattr(host.sys, "argv", ["bridge", "reconcile", "--key", KEY, "--output", str(path)])
    assert host.main() == 0 and json.loads(capsys.readouterr().out)["state"] == "interrupted"
    monkeypatch.setattr(host.sys, "argv", ["bridge", "reconcile", "--key", KEY, "--output", str(tmp_path / "other.json")])
    with pytest.raises(ValueError):
        host.main()
    assert not list(cg.rglob("dmem.low"))


def _status(tmp_path, monkeypatch):
    post = SimpleNamespace(root=tmp_path, executable=lambda game: tmp_path / "game.exe", host=None)
    game = SimpleNamespace(profile_key=KEY, install_path=tmp_path, launcher=SimpleNamespace(value="Steam"), launcher_variant="native")
    calls = []

    def call(*args, **_kwargs):
        calls.append(args[0])
        if args[0] == "probe":
            return {"gpus": [], "regions": {}, "readyRegions": [], "delegated": False,
                    "systemBooster": {"active": "inactive"}, "userBooster": {"active": "inactive"}}
        if args[0] == "reconcile":
            return host.reconcile_session(args[2], args[4])
        if args[0] == "snapshot":
            return host.snapshot(int(args[2]), "")
        raise AssertionError(args)

    monkeypatch.setattr(gm, "host_call", call)
    monkeypatch.setattr(gm, "vkd3d_tool", lambda *_: {})
    return gm.GpuMemoryService(post), game, calls


def test_status_reports_interrupted_history_without_current_protection(tmp_path, cg, monkeypatch) -> None:
    pid, ticks = _dead_pid()
    directory = gm.profile_path(KEY, tmp_path).parent
    directory.mkdir(parents=True)
    path = directory / "gpu-memory-session.json"
    path.write_text(json.dumps({"key": KEY, **_started(pid, ticks)}))
    service, game, calls = _status(tmp_path, monkeypatch)
    for _ in range(3):  # repeated status calls
        result = service.status(game)
        assert result["sessionState"] == "interrupted" and result["interruptedSession"] is True
        assert result["session"]["protectionVerified"] is False and result["runtimeProtectionVerified"] is False
        assert result["runtimeState"] == "not_running"
    assert json.loads(path.read_text())["status"] == "interrupted"
    assert "snapshot" not in calls  # nothing is sampled for a finished session


def _run_wrapper(tmp_path: Path, output: Path, seconds: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["/usr/bin/python3", str(HOST), "run", "--key", KEY, "--region", "", "--output", str(output), "--protect", "0",
         "--", "/bin/sleep", seconds],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))


needs_scope = pytest.mark.skipif(
    not shutil.which("systemd-run") or subprocess.run(["systemctl", "--user", "is-system-running"],
                                                      capture_output=True).returncode not in (0, 1),
    reason="needs a systemd user manager")


def _wait(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return predicate()


@needs_scope
@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_real_wrapper_signal_then_read_time_recovery(tmp_path, number) -> None:
    output = tmp_path / "gpu-memory-session.json"
    wrapper = _run_wrapper(tmp_path, output, "3")
    assert _wait(lambda: output.is_file() and json.loads(output.read_text()).get("status") == "observing")
    wrapper.send_signal(number)
    assert wrapper.wait(timeout=10) == -number  # the caller still sees the signal exit status
    record = json.loads(output.read_text())
    assert record["status"] == "observing" and record["wrapperSignal"] == signal.Signals(number).name
    assert record["wrapperPid"] == wrapper.pid
    # The detached game is still running in its scope: nothing is finalized yet.
    assert host.reconcile_session(output, KEY)["state"] == "running"
    view = _wait(lambda: (v := host.reconcile_session(output, KEY))["state"] == "interrupted" and v)
    assert view and view["persisted"] is True
    assert json.loads(output.read_text())["reconciliation"]["reason"] == "wrapper_interrupted_after_launch"


@needs_scope
def test_real_normal_exit_keeps_the_wrapper_final_state(tmp_path) -> None:
    output = tmp_path / "gpu-memory-session.json"
    wrapper = _run_wrapper(tmp_path, output, "0.2")
    assert wrapper.wait(timeout=20) == 0
    record = json.loads(output.read_text())
    assert record["status"] == "exited" and record["exitCode"] == 0 and "wrapperSignal" not in record
    assert host.reconcile_session(output, KEY) == {"state": "exited", "record": record, "persisted": False}
