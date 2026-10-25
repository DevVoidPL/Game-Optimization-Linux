"""Regression (audit 2026-10-16): a launcher-integration record from an older
Apply must never become the restore point after the launcher file changed.

Only temporary fixtures; no real Heroic config is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services.launcher_integration import (
    LauncherIntegrationService,
    LauncherPlan,
)

APP = "fa4240e57a3c46b39f169041b7811293"
PLAN = LauncherPlan(prefix=("gamemoderun",), environment={"WINEDLLOVERRIDES": "dxgi=n,b"})


def _game() -> Game:
    return Game(
        id=f"heroic-legendary-{APP}", name="Test", launcher=Launcher.HEROIC,
        install_path=Path("/games/test"), logical_size_gb=1.0, physical_size_gb=1.0,
        filesystem=FilesystemType.EXT4, compression_available=False,
        launcher_game_id=APP, launcher_variant="native",
    )


def _section(prefix: str, runner: str, gamemode: bool, **extra) -> dict:
    return {
        "winePrefix": prefix,
        "wineVersion": {"name": runner, "type": "proton", "bin": f"/tools/{runner}/proton"},
        "useGameMode": gamemode,
        "enviromentOptions": [],
        "wrapperOptions": [],
        **extra,
    }


def _write(path: Path, section: dict, *, indent: int = 2) -> None:
    path.write_text(json.dumps({APP: section, "version": "v0"}, indent=indent), encoding="utf-8")


def _setup(tmp_path: Path) -> tuple[Path, LauncherIntegrationService]:
    directory = tmp_path / ".config/heroic/GamesConfig"
    directory.mkdir(parents=True)
    path = directory / f"{APP}.json"
    service = LauncherIntegrationService(
        tmp_path / "state", lambda *_a, **_k: PLAN, home=tmp_path, process_commands=lambda: (),
    )
    return path, service


def _state(path: Path) -> dict:
    return json.loads(path.read_text())[APP]


def test_restore_returns_to_state_before_the_new_apply_not_the_old_record(tmp_path: Path) -> None:
    path, service = _setup(tmp_path)
    game = _game()
    # 2.10: old prefix, GE-Proton11-1, useGameMode on; GameOpti applies.
    _write(path, _section("/prefixes/Dziedzictwo Hogwartu", "GE-Proton11-1", True))
    service.apply(game)
    # Later the user changes the game in Heroic; Heroic rewrites the file and
    # GameOpti's old entries are gone.
    _write(path, _section("/prefixes/shared", "Proton-EM Latest", False, verboseLogs=True), indent=4)
    before_new_apply = path.read_text()
    assert service.status(game)["state"] == "modified"

    assert service.apply(game)["state"] == "applied"
    applied = _state(path)
    assert applied["wrapperOptions"] == [{"exe": "gamemoderun", "args": ""}]
    assert applied["winePrefix"] == "/prefixes/shared"

    service.restore(game)
    assert path.read_text() == before_new_apply  # byte-exact, not the 2.10 file
    restored = _state(path)
    assert restored["winePrefix"] == "/prefixes/shared"
    assert restored["wineVersion"]["name"] == "Proton-EM Latest"
    assert restored["useGameMode"] is False and restored["verboseLogs"] is True
    # The superseded record is kept as history.
    backups = tmp_path / "state/launcher-integration/backups" / game.id
    assert list(backups.glob("superseded-*.record.json"))


def test_stale_record_with_entries_still_present_drops_them_from_the_new_original(
    tmp_path: Path,
) -> None:
    """Heroic rewrote the file but kept GameOpti's old entries (real case 15:57)."""
    path, service = _setup(tmp_path)
    game = _game()
    _write(path, _section("/prefixes/old", "GE-Proton11-1", True))
    service.apply(game)
    rewritten = _state(path)
    rewritten.update(winePrefix="/prefixes/shared", useGameMode=False)
    _write(path, rewritten, indent=4)

    service.apply(game)
    service.restore(game)
    restored = _state(path)
    assert restored["winePrefix"] == "/prefixes/shared" and restored["useGameMode"] is False
    assert restored["enviromentOptions"] == [] and restored["wrapperOptions"] == []


def test_change_after_the_new_apply_still_removes_only_gameopti_entries(tmp_path: Path) -> None:
    path, service = _setup(tmp_path)
    game = _game()
    _write(path, _section("/prefixes/old", "GE-Proton11-1", True))
    service.apply(game)
    _write(path, _section("/prefixes/shared", "Proton-EM Latest", False))
    service.apply(game)
    edited = _state(path)
    edited["language"] = "pl"
    _write(path, edited, indent=4)

    service.restore(game)
    restored = _state(path)
    assert restored["language"] == "pl"
    assert restored["winePrefix"] == "/prefixes/shared" and restored["useGameMode"] is False
    assert restored["enviromentOptions"] == [] and restored["wrapperOptions"] == []


def test_untouched_file_keeps_the_first_original(tmp_path: Path) -> None:
    path, service = _setup(tmp_path)
    game = _game()
    _write(path, _section("/prefixes/shared", "Proton-EM Latest", False))
    first = path.read_text()
    service.apply(game)
    service.apply(game)  # same file, re-apply: the first original stays
    service.restore(game)
    assert path.read_text() == first
