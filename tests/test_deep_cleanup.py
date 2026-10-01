"""Deep-cleanup quarantine executor: every file operation stays inside tmp_path."""

from __future__ import annotations

from concurrent.futures import wait
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

from game_optimization_linux.models import Launcher
from game_optimization_linux.models.deep_optimization import DeepItem
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.services import deep_cleanup as cleanup_module
from game_optimization_linux.services.deep_cleanup import (
    MANIFEST_NAME,
    CleanupError,
    CleanupJournal,
    DeepCleanupExecutor,
)
from game_optimization_linux.services.deep_optimization import (
    DeepAnalysisRepository,
    DeepOptimizationAnalyzer,
)
from game_optimization_linux.services.game_analysis import GameAnalyzer
from game_optimization_linux.services.game_executable import GameExecutableResolver

APP_ID = "4242"
OLD = 1_000_000_000
SELECT = ["cache/crash_dumps", "cache/logs_old", "languages/lang:de", "residues/redistributables"]
FORBIDDEN = [
    "languages/lang:pl",          # target language
    "audio/lang:en",              # fallback audio (no Polish voice)
    "audio/shared",               # shared/neutral audio
    "textures/packages_opaque",   # core archives
    "textures/texture_files",     # textures/mipmaps
    "cache/steam_shadercache_uninstalled",  # scope=global
    "cache/compatdata_active",    # active Proton prefix
    "residues/protected_backups",  # user data
    "btrfs/compressible",         # compression is not part of this executor
    "audio/lang:xx",              # unknown group
]


class Crash(BaseException):
    """Simulated process death: not caught by ``except Exception``."""


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _setup(tmp_path: Path, **executor_kwargs: Any):
    library = tmp_path / "SteamLibrary"
    root = library / "steamapps" / "common" / "Game"
    files = {
        "loc/strings_de.txt": b"de-strings", "loc/strings_pl.txt": b"pl", "loc/strings_en.txt": b"en",
        "audio/en_US/vo.pck": b"voice" * 4, "common/music.pck": b"music",
        "Logs/old.log": b"old-log", "Logs/sub/older.log": b"older", "Crashes/a.dmp": b"dump",
        "_CommonRedist/vc/vc_redist.exe": b"MZredist", "Paks/core.pak": b"pak" * 10,
        "Textures/rock_low.dds": b"dds", "Saves/slot.sav.bak": b"save", "Game.exe": b"MZgame",
    }
    for relative, data in files.items():
        _write(root / relative, data)
    for relative in ("Logs/old.log", "Logs/sub/older.log", "Crashes/a.dmp"):
        os.utime(root / relative, ns=(OLD * 10**9, OLD * 10**9))
    os.chmod(root / "Logs/old.log", 0o640)
    (library / "steamapps" / f"appmanifest_{APP_ID}.acf").write_text('"AppState" {}', encoding="utf-8")
    _write(library / "steamapps/shadercache/999/a.foz", b"global")
    _write(library / f"steamapps/compatdata/{APP_ID}/pfx/user.reg", b"prefix")
    game = replace(demo_games()[0], id=f"steam-{APP_ID}", install_path=root, library_path=library,
                   steam_app_id=APP_ID, launcher=Launcher.STEAM)
    analyzer = DeepOptimizationAnalyzer(
        engine_detector=GameAnalyzer(GameExecutableResolver()).detect_engine,
        system_language=lambda: "pl", running_detector=lambda _root: (), allow_native_btrfs_du=False,
    )
    repository = DeepAnalysisRepository(tmp_path / "data" / "deep-analysis")
    repository.save(analyzer.analyze(game))
    plan = repository.load(game.id)
    executor_kwargs.setdefault("running_detector", lambda _root: ())
    executor = DeepCleanupExecutor(CleanupJournal(tmp_path / "data" / "deep-cleanup"), **executor_kwargs)
    return game, root, library, repository, plan, executor


def _snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode))
        for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
    }


def _quarantine(executor, game, repository, plan, groups=SELECT, **kwargs):
    transaction = executor.prepare(game, plan, repository.fingerprint(plan), groups)
    return executor.quarantine(transaction, **kwargs)


# A. Forbidden groups ----------------------------------------------------------------------


def test_a_forbidden_groups_are_never_selectable(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    before = _snapshot(root)
    assert set(SELECT) <= set(executor.selectable_groups(plan))
    for group_id in FORBIDDEN:
        assert group_id not in executor.selectable_groups(plan)
        with pytest.raises(CleanupError) as error:
            executor.prepare(game, plan, repository.fingerprint(plan), [group_id, "cache/crash_dumps"])
        assert error.value.code == "not_selectable"
    groups = {group.group_id: group for group in plan.groups}
    assert groups["languages/lang:pl"].role == "target" and groups["audio/lang:en"].role == "fallback"
    assert _snapshot(root) == before
    assert executor.journal.all() == []


def test_a_controller_rejects_forbidden_groups_without_qml(tmp_path: Path) -> None:
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.providers import DemoGameProvider
    from game_optimization_linux.services import MockTaskService, SettingsStore

    game, root, _library, repository, _plan, executor = _setup(tmp_path)
    before = _snapshot(root)
    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        deep_analysis_repository=repository, deep_cleanup_executor=executor,
        initial_games=(game,), auto_refresh=False,
    )
    try:
        for group_id in FORBIDDEN:
            assert controller.startDeepQuarantine(game.id, [group_id])["success"] is True
            operation = controller._deep_cleanup_controller._operations[game.id]
            wait([operation.future], timeout=5)
            controller._deep_cleanup_controller._poll()
            result = controller.getDeepCleanup(game.id)["result"]
            assert (result["success"], result["code"]) == (False, "not_selectable")
    finally:
        controller.shutdown()
    assert _snapshot(root) == before


# B. Stale plan -----------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["size", "mtime"])
def test_b_changed_file_blocks_everything_before_the_first_move(tmp_path: Path, change: str) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    target = root / "loc/strings_de.txt"
    if change == "size":
        target.write_bytes(b"de-strings-updated")
    else:
        os.utime(target, ns=(OLD * 10**9, OLD * 10**9))
    before = _snapshot(root)
    with pytest.raises(CleanupError) as error:
        _quarantine(executor, game, repository, plan)
    assert error.value.code == "plan_stale" and error.value.stale is True
    assert _snapshot(root) == before
    assert not (root.parent.parent / ".gameoptimization-quarantine").exists()
    assert repository.mark_stale(game.id).stale is True
    with pytest.raises(CleanupError) as again:
        _quarantine(executor, game, repository, repository.load(game.id))
    assert again.value.code == "plan_stale"


# C. Traversal, symlinks, replaced root ---------------------------------------------------------


def test_c_path_traversal_and_symlinks_are_blocked(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    info = outside.stat()
    traversal = replace(plan, items={**plan.items, "cache/crash_dumps": (
        DeepItem("../../../../outside.txt", info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino),)})
    with pytest.raises(CleanupError) as error:
        executor.prepare(game, traversal, "x", ["cache/crash_dumps"])
    assert error.value.code == "unsafe_path"
    # Symlink replacing a selected file.
    dump = root / "Crashes/a.dmp"
    dump.unlink()
    dump.symlink_to(outside)
    with pytest.raises(CleanupError) as error:
        executor.prepare(game, plan, "x", ["cache/crash_dumps"])
    assert error.value.code in {"unsafe_path", "plan_stale"}
    # Symlinked parent directory pointing outside the game.
    elsewhere = tmp_path / "elsewhere"
    (root / "Logs").rename(elsewhere)
    (root / "Logs").symlink_to(elsewhere)
    with pytest.raises(CleanupError) as error:
        executor.prepare(game, plan, "x", ["cache/logs_old"])
    assert error.value.code == "unsafe_path"
    assert outside.read_bytes() == b"secret" and (elsewhere / "old.log").exists()


def test_c_replaced_game_root_is_blocked(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    root.rename(root.with_name("Game-original"))
    root.mkdir()
    with pytest.raises(CleanupError) as error:
        _quarantine(executor, game, repository, plan)
    assert error.value.code == "game_root_changed"


# D. Other device -----------------------------------------------------------------------------


def test_d_quarantine_on_another_device_is_blocked_without_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game, root, library, repository, plan, executor = _setup(tmp_path)
    real_lstat = os.lstat
    base = library / "steamapps" / ".gameoptimization-quarantine" / APP_ID

    def fake_lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if Path(path).parent == base:
            values = list(result)
            values[2] = result.st_dev + 7
            return os.stat_result(values)
        return result

    monkeypatch.setattr(cleanup_module.os, "lstat", fake_lstat)
    before = _snapshot(root)
    with pytest.raises(CleanupError) as error:
        _quarantine(executor, game, repository, plan)
    monkeypatch.undo()
    assert error.value.code == "quarantine_other_device"
    assert _snapshot(root) == before
    assert list(base.iterdir()) == []  # the transaction directory was removed, nothing copied
    assert [item.state for item in executor.journal.all()] == ["restored"]


# E. Successful quarantine ---------------------------------------------------------------------


def test_e_quarantine_keeps_content_metadata_and_structure(tmp_path: Path) -> None:
    game, root, library, repository, plan, executor = _setup(tmp_path)
    before = _snapshot(root)
    progress: list[Any] = []
    transaction = _quarantine(executor, game, repository, plan, progress=progress.append)
    assert transaction.state == "quarantined"
    quarantine = Path(transaction.quarantine_root)
    assert quarantine.parent == library / "steamapps" / ".gameoptimization-quarantine" / APP_ID
    assert stat.S_IMODE(quarantine.stat().st_mode) == 0o700
    assert quarantine.stat().st_dev == root.stat().st_dev
    moved = {entry.relative_path for entry in transaction.entries}
    assert moved == {"Crashes/a.dmp", "Logs/old.log", "Logs/sub/older.log", "loc/strings_de.txt",
                     "_CommonRedist/vc/vc_redist.exe"}
    for relative in moved:
        assert not (root / relative).exists()
        target = quarantine / "files" / relative
        assert (target.read_bytes(), target.stat().st_mtime_ns, stat.S_IMODE(target.stat().st_mode)) == before[relative]
    assert (root / "Logs/sub").is_dir()  # empty game directories stay
    manifest = json.loads((quarantine / MANIFEST_NAME).read_text())
    assert manifest["transactionId"] == transaction.transaction_id and str(tmp_path) not in json.dumps(manifest)
    assert progress[-1].files == len(moved) and progress[-1].bytes == transaction.total_bytes
    assert executor.journal.for_game(game.id)[0].state == "quarantined"


# F. Rollback --------------------------------------------------------------------------------------


def test_f_failure_mid_way_rolls_back_in_reverse_order(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    state = {"count": 0}

    def rename(source: Path, target: Path) -> None:
        state["count"] += 1
        if state["count"] == 3:
            raise OSError("injected failure")
        calls.append((str(source), str(target)))
        os.rename(source, target)

    links: list[str] = []

    def link(source: Path, target: Path) -> None:
        links.append(str(target))
        os.link(source, target)

    game, root, _library, repository, plan, executor = _setup(tmp_path, rename=rename, link=link)
    before = _snapshot(root)
    transaction = _quarantine(executor, game, repository, plan)
    assert transaction.state == "failed" and transaction.note == "OSError"
    moved_order = [source for source, _target in calls]
    assert links == list(reversed(moved_order))  # restored in reverse order
    assert _snapshot(root) == before
    assert not Path(transaction.quarantine_root).exists()


def test_f_failed_rollback_is_partial_never_success(tmp_path: Path) -> None:
    state = {"count": 0}

    def rename(source: Path, target: Path) -> None:
        state["count"] += 1
        if state["count"] == 3:
            raise OSError("injected failure")
        os.rename(source, target)

    def link(_source: Path, _target: Path) -> None:
        raise OSError(5, "I/O error during rollback")

    game, root, _library, repository, plan, executor = _setup(tmp_path, rename=rename, link=link)
    transaction = _quarantine(executor, game, repository, plan)
    assert transaction.state == "partial"
    counts = transaction.counts()
    assert counts["quarantined"] == 2 and counts["error"] == 1
    for entry in transaction.entries:
        if entry.status == "quarantined":
            assert (Path(transaction.quarantine_root) / "files" / entry.relative_path).exists()


def test_cancel_between_files_rolls_back(tmp_path: Path) -> None:
    from threading import Event

    game, root, _library, repository, plan, executor = _setup(tmp_path)
    before = _snapshot(root)
    cancel = Event()

    def progress(value) -> None:
        if value.files == 2:
            cancel.set()

    transaction = _quarantine(executor, game, repository, plan, cancel_event=cancel, progress=progress)
    assert (transaction.state, transaction.note) == ("restored", "cancelled")
    assert _snapshot(root) == before


# G. Restore conflicts ---------------------------------------------------------------------------


def test_g_restore_never_overwrites_a_redownloaded_file(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)
    (root / "loc/strings_de.txt").write_bytes(b"steam re-downloaded")
    restored = executor.restore(transaction, game)
    assert restored.state == "partial" and restored.note == "restore_conflicts"
    conflict = next(entry for entry in restored.entries if entry.relative_path == "loc/strings_de.txt")
    assert (conflict.status, conflict.conflict) == ("conflict", "different")
    assert (root / "loc/strings_de.txt").read_bytes() == b"steam re-downloaded"
    assert (Path(restored.quarantine_root) / "files/loc/strings_de.txt").read_bytes() == b"de-strings"
    assert (root / "Logs/old.log").read_bytes() == b"old-log"
    assert sum(entry.status == "restored" for entry in restored.entries) == 4


def test_g_full_restore_removes_only_this_transaction_directory(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    before = _snapshot(root)
    other = root.parent.parent / ".gameoptimization-quarantine" / APP_ID / "otherTransaction"
    transaction = _quarantine(executor, game, repository, plan)
    other.mkdir(mode=0o700)
    (other / "keep.bin").write_bytes(b"other")
    restored = executor.restore(transaction, game)
    assert restored.state == "restored"
    assert _snapshot(root) == before
    assert not Path(restored.quarantine_root).exists()
    assert (other / "keep.bin").read_bytes() == b"other"


# H. Finalize -----------------------------------------------------------------------------------


def test_h_finalize_requires_confirmation_and_deletes_only_journal_entries(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)
    quarantine = Path(transaction.quarantine_root)
    for word in ("", "usuń", "yes", "DELETE ALL"):
        with pytest.raises(CleanupError) as error:
            executor.finalize(transaction, game, word)
        assert error.value.code == "confirmation_required"
    assert all((quarantine / "files" / entry.relative_path).exists() for entry in transaction.entries)
    stranger = quarantine / "files" / "not-in-journal.bin"
    stranger.write_bytes(b"keep me")
    finalized = executor.finalize(transaction, game, "USUŃ")
    assert finalized.state == "finalized"
    assert all(entry.status == "deleted" for entry in finalized.entries)
    assert stranger.read_bytes() == b"keep me"  # unknown files are never deleted
    assert quarantine.exists()  # so the directory stays
    history = finalized.history_entry()
    assert history["gameRoot"] == "$GAME_ROOT" and str(tmp_path) not in json.dumps(history)
    assert (root / "Game.exe").exists() and (root / "loc/strings_pl.txt").exists()


def test_h_finalize_removes_empty_transaction_directory(tmp_path: Path) -> None:
    game, _root, _library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)
    finalized = executor.finalize(transaction, game, "DELETE")
    assert finalized.state == "finalized" and not Path(finalized.quarantine_root).exists()
    assert executor.journal.for_game(game.id)[0].state == "finalized"


# I. Crash recovery -------------------------------------------------------------------------------


def _crash_rename_after(count: int):
    state = {"count": 0}

    def rename(source: Path, target: Path) -> None:
        if state["count"] == count:
            raise Crash()
        state["count"] += 1
        os.rename(source, target)

    return rename


@pytest.mark.parametrize(("moved", "expected"), [(0, "restored"), (2, "partial"), (5, "quarantined")])
def test_i_crash_during_quarantine_is_reconciled(tmp_path: Path, moved: int, expected: str) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path, rename=_crash_rename_after(moved))
    transaction = executor.prepare(game, plan, repository.fingerprint(plan), SELECT)
    if moved < 5:
        with pytest.raises(Crash):
            executor.quarantine(transaction)
        assert executor.journal.for_game(game.id)[0].state == "quarantining"
    else:
        executor.quarantine(transaction)
        journal = executor.journal.for_game(game.id)[0]
        executor.journal.save(journal.with_state("quarantining"))  # crash before the final state write
    recovered = DeepCleanupExecutor(executor.journal).recover()
    assert len(recovered) == 1
    assert recovered[0].state == expected and recovered[0].recovered is True
    assert recovered[0].counts()["quarantined"] == moved


def test_i_crash_in_preparing_cancels_safely(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    before = _snapshot(root)
    executor.prepare(game, plan, repository.fingerprint(plan), SELECT)  # crash before quarantining
    recovered = DeepCleanupExecutor(executor.journal).recover()
    assert (recovered[0].state, recovered[0].note) == ("restored", "cancelled_before_start")
    assert _snapshot(root) == before and not Path(recovered[0].quarantine_root).exists()


def test_i_crash_during_restore_after_link_is_completed_safely(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)

    def crash_unlink(_path: Path) -> None:
        raise Crash()

    crashing = DeepCleanupExecutor(executor.journal, running_detector=lambda _root: (), unlink=crash_unlink)
    with pytest.raises(Crash):
        crashing.restore(transaction, game)
    recovered = DeepCleanupExecutor(executor.journal).recover()[0]
    linked = [entry for entry in recovered.entries if entry.status == "restored"]
    assert len(linked) == 1 and recovered.state == "partial"
    path = linked[0].relative_path
    assert (root / path).exists() and not (Path(recovered.quarantine_root) / "files" / path).exists()


def test_i_crash_during_finalize_never_resumes(tmp_path: Path) -> None:
    game, _root, _library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)
    state = {"count": 0}

    def unlink(path: Path) -> None:
        if state["count"] == 2:
            raise Crash()
        state["count"] += 1
        os.unlink(path)

    crashing = DeepCleanupExecutor(executor.journal, running_detector=lambda _root: (), unlink=unlink)
    with pytest.raises(Crash):
        crashing.finalize(transaction, game, "USUŃ")
    recovered = DeepCleanupExecutor(executor.journal).recover()[0]
    assert (recovered.state, recovered.note) == ("partial", "finalize_interrupted")
    counts = recovered.counts()
    assert counts["deleted"] == 2 and counts["quarantined"] == 3
    assert all((Path(recovered.quarantine_root) / "files" / entry.relative_path).exists()
               for entry in recovered.entries if entry.status == "quarantined")
    # A second recovery is a no-op: partial is settled and nothing is deleted.
    assert DeepCleanupExecutor(executor.journal).recover() == []
    with pytest.raises(CleanupError):
        executor.finalize(recovered, game, "USUŃ")


# J. Running / updating game ------------------------------------------------------------------------


def test_j_running_game_blocks_every_operation(tmp_path: Path) -> None:
    game, root, _library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)
    running = DeepCleanupExecutor(executor.journal, running_detector=lambda _root: (1234,))
    for action in (
        lambda: running.prepare(game, plan, repository.fingerprint(plan), SELECT),
        lambda: running.restore(transaction, game),
        lambda: running.finalize(transaction, game, "USUŃ"),
    ):
        with pytest.raises(CleanupError) as error:
            action()
        assert error.value.code == "game_running"
    assert executor.journal.for_game(game.id)[0].state == "quarantined"


@pytest.mark.parametrize("folder", ["downloading", "temp"])
def test_j_active_steam_download_blocks_every_operation(tmp_path: Path, folder: str) -> None:
    game, root, library, repository, plan, executor = _setup(tmp_path)
    transaction = _quarantine(executor, game, repository, plan)
    (library / "steamapps" / folder / APP_ID).mkdir(parents=True)
    for action in (
        lambda: executor.restore(transaction, game),
        lambda: executor.finalize(transaction, game, "USUŃ"),
    ):
        with pytest.raises(CleanupError) as error:
            action()
        assert error.value.code == "game_updating"
    fresh = repository.load(game.id)
    with pytest.raises(CleanupError) as error:
        executor.prepare(game, fresh, repository.fingerprint(fresh), ["cache/crash_dumps"])
    assert error.value.code in {"game_updating", "plan_stale"}


def test_j_launcher_update_detector_blocks(tmp_path: Path) -> None:
    game, _root, _library, repository, plan, executor = _setup(tmp_path, update_detector=lambda _game: True)
    with pytest.raises(CleanupError) as error:
        _quarantine(executor, game, repository, plan)
    assert error.value.code == "game_updating"
