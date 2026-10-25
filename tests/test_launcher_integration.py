"""Heroic/Lutris launcher integration: only the managed entries are changed."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.models.mangohud import validate_game_key
from game_optimization_linux.services.launcher_integration import (
    LauncherIntegrationService,
    LauncherPlan,
    LauncherSyncError,
)

APP_NAME = "fa4240e57a3c46b39f169041b7811293"
PLAN = LauncherPlan(
    prefix=("gamemoderun", "gamescope", "-W", "1920", "--"),
    environment={"MANGOHUD": "1", "WINEDLLOVERRIDES": "dxgi=n,b"},
)


def _game(launcher: Launcher, launcher_id: str, *, variant: str = "native") -> Game:
    prefix = "heroic-legendary" if launcher is Launcher.HEROIC else "lutris"
    return Game(
        id=f"{prefix}-{launcher_id}", name="Test", launcher=launcher,
        install_path=Path("/games/test"), logical_size_gb=1.0, physical_size_gb=1.0,
        filesystem=FilesystemType.BTRFS, compression_available=True,
        launcher_game_id=launcher_id, launcher_variant=variant,
    )


def _heroic(tmp_path: Path, section: dict | None = None) -> tuple[Path, Game]:
    config = tmp_path / ".config/heroic/GamesConfig"
    config.mkdir(parents=True)
    path = config / f"{APP_NAME}.json"
    path.write_text(json.dumps({
        APP_NAME: section if section is not None else {
            "enviromentOptions": [{"key": "KEEP", "value": "1"}],
            "wrapperOptions": [],
        },
        "version": "v0", "explicit": True,
    }, indent=2), encoding="utf-8")
    return path, _game(Launcher.HEROIC, APP_NAME)


def _service(tmp_path: Path, *, running: tuple[str, ...] | None = ()) -> LauncherIntegrationService:
    return LauncherIntegrationService(
        tmp_path / "state", lambda game, **_: PLAN, home=tmp_path,
        process_commands=lambda: running,
    )


def test_keys_for_launcher_games_are_valid_file_names() -> None:
    assert validate_game_key(f"heroic-legendary-{APP_NAME}")
    assert validate_game_key("lutris-12")
    for bad in ("heroic-..", "heroic-a/b", "lutris-", "x-heroic-1"):
        with pytest.raises(ValueError):
            validate_game_key(bad)


def test_profile_key_covers_steam_local_and_launchers() -> None:
    assert _game(Launcher.HEROIC, APP_NAME).profile_key == f"heroic-legendary-{APP_NAME}"
    assert _game(Launcher.LUTRIS, "7").profile_key == "lutris-7"


def test_heroic_apply_changes_only_managed_entries_and_restores(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    original = json.loads(path.read_text())
    service = _service(tmp_path)
    assert service.status(game)["state"] == "not_applied"

    status = service.apply(game)
    section = json.loads(path.read_text())[APP_NAME]
    assert status["state"] == "applied"
    env = {item["key"]: item["value"] for item in section["enviromentOptions"]}
    assert env == {"KEEP": "1", "MANGOHUD": "1", "WINEDLLOVERRIDES": "dxgi=n,b"}
    assert section["wrapperOptions"] == [{"exe": "gamemoderun", "args": "gamescope -W 1920 --"}]
    assert list((tmp_path / "state/launcher-integration/backups").rglob("*.bak"))

    status = service.restore(game)
    assert status["state"] == "not_applied"
    assert json.loads(path.read_text()) == original


def test_heroic_user_edit_after_apply_is_left_alone_on_restore(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    service = _service(tmp_path)
    service.apply(game)
    document = json.loads(path.read_text())
    for item in document[APP_NAME]["enviromentOptions"]:
        if item["key"] == "MANGOHUD":
            item["value"] = "user"
    document[APP_NAME]["enviromentOptions"].append({"key": "NEW", "value": "n"})
    path.write_text(json.dumps(document))
    assert service.status(game)["state"] == "modified"

    service.restore(game)
    env = {item["key"]: item["value"] for item in json.loads(path.read_text())[APP_NAME]["enviromentOptions"]}
    assert env["MANGOHUD"] == "user" and env["NEW"] == "n" and "WINEDLLOVERRIDES" not in env


def test_heroic_existing_dll_overrides_are_merged(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path, {
        "enviromentOptions": [{"key": "WINEDLLOVERRIDES", "value": "mscoree=d"}],
        "wrapperOptions": [],
    })
    _service(tmp_path).apply(game)
    env = {item["key"]: item["value"] for item in json.loads(path.read_text())[APP_NAME]["enviromentOptions"]}
    assert "mscoree=d" in env["WINEDLLOVERRIDES"] and "dxgi=n,b" in env["WINEDLLOVERRIDES"]


def test_outdated_when_the_plan_changes_and_apply_replaces_old_entries(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    plans = [PLAN]
    service = LauncherIntegrationService(
        tmp_path / "state", lambda game, **_: plans[0], home=tmp_path, process_commands=lambda: (),
    )
    service.apply(game)
    plans[0] = LauncherPlan(prefix=("gamemoderun",), environment={"MANGOHUD": "1"})
    assert service.status(game)["state"] == "outdated"
    service.apply(game)
    section = json.loads(path.read_text())[APP_NAME]
    assert [item["exe"] for item in section["wrapperOptions"]] == ["gamemoderun"]
    assert "WINEDLLOVERRIDES" not in {item["key"] for item in section["enviromentOptions"]}


def test_nothing_is_written_while_the_launcher_runs(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    before = path.read_text()
    service = _service(tmp_path, running=("/app/bin/heroic/heroic --type=zygote",))
    status = service.status(game)
    assert status["launcherRunning"] and not status["canApply"]
    with pytest.raises(LauncherSyncError, match="Close Heroic"):
        service.apply(game)
    assert path.read_text() == before


def test_missing_heroic_settings_are_reported_not_created(tmp_path: Path) -> None:
    game = _game(Launcher.HEROIC, APP_NAME)
    status = _service(tmp_path).status(game)
    assert status["state"] == "unavailable" and "open the game's settings" in status["message"]
    with pytest.raises(LauncherSyncError):
        _service(tmp_path).apply(game)


def test_symlinked_launcher_config_is_refused(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text(path.read_text())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(LauncherSyncError, match="regular file"):
        _service(tmp_path).apply(game)


def _lutris(tmp_path: Path, system: dict | None = None, *, variant: str = "native") -> tuple[Path, Game]:
    root = tmp_path / ".config/lutris"
    data = tmp_path / ".local/share/lutris"
    (root / "games").mkdir(parents=True)
    data.mkdir(parents=True)
    connection = sqlite3.connect(data / "pga.db")
    connection.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, configpath TEXT)")
    connection.execute("INSERT INTO games VALUES (7, 'test-123')")
    connection.commit()
    connection.close()
    path = root / "games/test-123.yml"
    document: dict = {"game": {"exe": "/games/test/a.exe"}}
    if system is not None:
        document["system"] = system
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path, _game(Launcher.LUTRIS, "7", variant=variant)


def test_lutris_apply_prepends_prefix_command_and_restores(tmp_path: Path) -> None:
    path, game = _lutris(tmp_path, {"env": {"KEEP": "1"}})
    original = yaml.safe_load(path.read_text())
    service = _service(tmp_path)
    assert service.apply(game)["state"] == "applied"
    system = yaml.safe_load(path.read_text())["system"]
    assert system["prefix_command"] == "gamemoderun gamescope -W 1920 --"
    assert system["env"] == {"KEEP": "1", "MANGOHUD": "1"}
    # Lutris' wine runner rebuilds WINEDLLOVERRIDES from wine.overrides.
    assert yaml.safe_load(path.read_text())["wine"]["overrides"] == {"dxgi": "n,b"}

    service.restore(game)
    assert yaml.safe_load(path.read_text()) == original


def test_lutris_without_a_system_section_is_created_and_removed_again(tmp_path: Path) -> None:
    path, game = _lutris(tmp_path)
    original = yaml.safe_load(path.read_text())
    service = _service(tmp_path)
    service.apply(game)
    assert "system" in yaml.safe_load(path.read_text())
    service.restore(game)
    assert yaml.safe_load(path.read_text()).get("system", {}) == {}
    assert yaml.safe_load(path.read_text())["game"] == original["game"]


def test_lutris_flatpak_variant_uses_the_flatpak_directories(tmp_path: Path) -> None:
    base = tmp_path / ".var/app/net.lutris.Lutris"
    (base / "config/lutris/games").mkdir(parents=True)
    (base / "data/lutris").mkdir(parents=True)
    connection = sqlite3.connect(base / "data/lutris/pga.db")
    connection.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, configpath TEXT)")
    connection.execute("INSERT INTO games VALUES (7, 'f-1')")
    connection.commit()
    connection.close()
    (base / "config/lutris/games/f-1.yml").write_text("game: {}\n", encoding="utf-8")
    status = _service(tmp_path).status(_game(Launcher.LUTRIS, "7", variant="flatpak"))
    assert status["state"] == "not_applied" and status["sandboxed"] is True

def _read_only_heroic(tmp_path: Path) -> tuple[Path, Game]:
    path, game = _heroic(tmp_path)
    path.parent.chmod(0o555)
    path.chmod(0o444)
    return path, game


def _host_mv(calls: list[tuple[Path, Path]]):
    """Stand-in for HostServiceClient.replace_file: a host rename that ignores
    the sandbox's read-only mount (here: the read-only directory mode)."""

    def replace_file(staged: Path, target: Path) -> None:
        calls.append((staged, target))
        target.parent.chmod(0o755)
        try:
            os.replace(staged, target)
        finally:
            target.parent.chmod(0o555)

    return replace_file


def test_read_only_config_is_written_through_the_host_bridge(tmp_path: Path) -> None:
    path, game = _read_only_heroic(tmp_path)
    original = path.read_text()
    calls: list[tuple[Path, Path]] = []
    service = LauncherIntegrationService(
        tmp_path / "state", lambda game, **_: PLAN, home=tmp_path,
        process_commands=lambda: (), host_writer=_host_mv(calls),
    )
    status = service.status(game)
    assert status["writeVia"] == "host-bridge" and status["canApply"] is True

    assert service.apply(game)["state"] == "applied"
    assert len(calls) == 1 and calls[0][1] == path
    assert not calls[0][0].exists()  # staged file was renamed, nothing left behind
    section = json.loads(path.read_text())[APP_NAME]
    assert section["wrapperOptions"][-1] == {"exe": "gamemoderun", "args": "gamescope -W 1920 --"}

    service.restore(game)
    assert path.read_text() == original  # exact original bytes are back
    assert len(calls) == 2
    path.parent.chmod(0o755)


def test_read_only_config_without_bridge_is_reported_not_faked(tmp_path: Path) -> None:
    path, game = _read_only_heroic(tmp_path)
    before = path.read_text()
    service = LauncherIntegrationService(
        tmp_path / "state", lambda game, **_: PLAN, home=tmp_path,
        process_commands=lambda: (), host_writer=None,
    )
    status = service.status(game)
    assert status["canApply"] is False and status["state"] == "not_applied"
    assert "read-only" in status["message"]
    with pytest.raises(LauncherSyncError, match="read-only"):
        service.apply(game)
    assert path.read_text() == before
    path.parent.chmod(0o755)


def test_failed_host_write_is_an_error_not_applied(tmp_path: Path) -> None:
    path, game = _read_only_heroic(tmp_path)
    before = path.read_text()

    def broken(staged: Path, target: Path) -> None:
        raise RuntimeError("mv: cannot move")

    service = LauncherIntegrationService(
        tmp_path / "state", lambda game, **_: PLAN, home=tmp_path,
        process_commands=lambda: (), host_writer=broken,
    )
    with pytest.raises(LauncherSyncError, match="host bridge"):
        service.apply(game)
    assert path.read_text() == before
    assert service.status(game)["state"] == "not_applied"
    path.parent.chmod(0o755)


def test_plan_blockers_disable_apply_with_the_reason(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    reason = "Gamescope is not available inside the Heroic Flatpak"
    plan = LauncherPlan(prefix=PLAN.prefix, environment=PLAN.environment, blockers=(reason,))
    service = LauncherIntegrationService(
        tmp_path / "state", lambda game, **_: plan, home=tmp_path, process_commands=lambda: (),
    )
    status = service.status(game)
    assert status["canApply"] is False and status["message"] == reason
    with pytest.raises(LauncherSyncError, match="Gamescope"):
        service.apply(game)


def test_original_backup_survives_many_apply_restore_cycles(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    original = path.read_text()
    service = _service(tmp_path)
    for _ in range(8):
        service.apply(game)
        service.restore(game)
    assert path.read_text() == original
    service.apply(game)
    service.apply(game)  # re-apply keeps the first original
    record = json.loads((tmp_path / "state/launcher-integration" / f"{game.id}.json").read_text())
    assert Path(record["originalBackupPath"]).read_text() == original
    service.restore(game)
    assert path.read_text() == original


def test_user_edit_after_apply_is_kept_on_restore(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    service = _service(tmp_path)
    service.apply(game)
    document = json.loads(path.read_text())
    document[APP_NAME]["language"] = "pl"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    assert service.status(game)["editedOutside"] is True
    service.restore(game)
    section = json.loads(path.read_text())[APP_NAME]
    assert section["language"] == "pl"
    assert section["wrapperOptions"] == []
    assert {item["key"]: item["value"] for item in section["enviromentOptions"]} == {"KEEP": "1"}


def test_version_1_record_is_still_restorable(tmp_path: Path) -> None:
    path, game = _heroic(tmp_path)
    service = _service(tmp_path)
    service.apply(game)
    record_path = tmp_path / "state/launcher-integration" / f"{game.id}.json"
    record = json.loads(record_path.read_text())
    for key in ("originalBackupPath", "originalDigest", "appliedDigest", "dll"):
        record.pop(key)
    record["version"] = 1
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert service.status(game)["canRestore"] is True
    service.restore(game)
    section = json.loads(path.read_text())[APP_NAME]
    assert section["wrapperOptions"] == []




# -- controller level: the tabs work for launcher games --------------------------
from dataclasses import replace  # noqa: E402

from game_optimization_linux.controllers import AppController  # noqa: E402
from game_optimization_linux.models import GameOptimizationProfile  # noqa: E402
from game_optimization_linux.providers import DemoGameProvider  # noqa: E402
from game_optimization_linux.services import SettingsStore  # noqa: E402
from game_optimization_linux.services.mangohud import MangoHudProfileRepository  # noqa: E402
from game_optimization_linux.services.optimization_profiles import (  # noqa: E402
    GameOptimizationProfileRepository,
)
from game_optimization_linux.services.optimization_runtime import (  # noqa: E402
    RuntimeToolAvailability,
)
from game_optimization_linux.services.proton_tweaks import ProtonTweaksRepository  # noqa: E402


class _Detector:
    def detect(self, *, refresh: bool = False):
        return (
            RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun"),
            RuntimeToolAvailability("Gamescope", False),
        )


def test_optimization_tab_backend_works_for_a_heroic_game_and_applies(tmp_path: Path) -> None:
    config, game = _heroic(tmp_path)
    game = replace(game, library_available=True)
    profiles = GameOptimizationProfileRepository(tmp_path / "profiles")
    profiles.save(replace(GameOptimizationProfile.default(game.id), gamemode_enabled=True))
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=profiles,
        mangohud_repository=MangoHudProfileRepository(tmp_path / "mango"),
        proton_tweaks_repository=ProtonTweaksRepository(tmp_path / "proton"),
        runtime_tool_detector=_Detector(),
        launcher_integration=LauncherIntegrationService(
            tmp_path / "state", lambda *_a, **_k: None, home=tmp_path, process_commands=lambda: (),
        ),
        initial_games=(game,),
        auto_refresh=False,
    )
    service = LauncherIntegrationService(
        tmp_path / "state", controller._optimization_controller.launcher_plan,
        home=tmp_path, process_commands=lambda: (),
    )
    controller._launcher_integration = service

    profile = controller.getOptimizationProfile(game.id)
    assert profile["success"] is True and profile["launcherGame"] is True
    assert profile["launchActivation"]["state"] == "launcher_not_applied"
    assert profile["launchActivation"]["prefix"] == ["/usr/bin/gamemoderun"]

    result = controller.applyLauncherIntegration(game.id)
    assert result["success"] is True and result["state"] == "applied"
    wrappers = json.loads(config.read_text())[APP_NAME]["wrapperOptions"]
    assert wrappers[-1] == {"exe": "/usr/bin/gamemoderun", "args": ""}
    assert controller.getOptimizationProfile(game.id)["launchActivation"]["state"] == "active"

    assert controller.restoreLauncherIntegration(game.id)["state"] == "not_applied"
    controller.shutdown()
