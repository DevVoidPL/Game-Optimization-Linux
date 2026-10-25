"""Heroic + OptiScaler: capabilities, explicit executable choice, launcher
integration safety and a non-blocking sandbox check.

Only temporary fixtures/XDG roots are used; no real game or Heroic config.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event
import time
from zipfile import ZipFile

import pytest

from game_optimization_linux.models import (
    FilesystemType,
    Game,
    GameOptimizationProfile,
    Launcher,
)
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.controllers import AppController
from game_optimization_linux.services import (
    GameExecutableResolver,
    OptiScalerError,
    OptiScalerProfileRepository,
    OptiScalerService,
    SettingsStore,
)
from game_optimization_linux.services.game_capabilities import game_capabilities
from game_optimization_linux.services.launcher_integration import (
    LauncherIntegrationService,
    LauncherPlan,
    LauncherSyncError,
    launcher_facts,
)
from game_optimization_linux.services.library_cache import LibraryCache
from game_optimization_linux.services.mangohud import MangoHudProfileRepository
from game_optimization_linux.services.optimization_profiles import (
    GameOptimizationProfileRepository,
)
from game_optimization_linux.services.optimization_runtime import RuntimeToolAvailability
from game_optimization_linux.services.proton_tweaks import ProtonTweaksRepository

APP_NAME = "abc123def456"
GAME_ID = f"heroic-legendary-{APP_NAME}"


# -- fixtures ------------------------------------------------------------------


def _unreal_layout(root: Path) -> Path:
    """A launcher stub at the top and the real binary in <Project>/Binaries/Win64,
    plus an Engine helper; names are synthetic on purpose."""

    (root / "SpellProject/Binaries/Win64").mkdir(parents=True)
    (root / "Engine/Binaries/Win64").mkdir(parents=True)
    (root / "SpellGame.exe").write_bytes(b"stub")
    (root / "SpellProject/Binaries/Win64/SpellGame.exe").write_bytes(b"real")
    (root / "Engine/Binaries/Win64/CrashReportClient.exe").write_bytes(b"helper")
    return root


def _heroic_game(tmp_path: Path, *, variant: str = "native") -> Game:
    root = _unreal_layout(tmp_path / "games/SpellGame")
    prefix = tmp_path / "prefixes/shared"
    prefix.mkdir(parents=True)
    return Game(
        id=GAME_ID, name="Spell Game", launcher=Launcher.HEROIC,
        install_path=root, logical_size_gb=1.0, physical_size_gb=1.0,
        filesystem=FilesystemType.EXT4, compression_available=False,
        data_source="Heroic", launcher_game_id=APP_NAME, launcher_variant=variant,
        store="Epic", runner="legendary", wine_prefix=prefix,
        executable_path=str(root / "SpellGame.exe"),
        executable_resolution="launcher_metadata",
    )


def _heroic_config(tmp_path: Path, section: dict | None = None) -> Path:
    directory = tmp_path / ".config/heroic/GamesConfig"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{APP_NAME}.json"
    body = {
        "enviromentOptions": [{"key": "KEEP", "value": "1"}],
        "wrapperOptions": [],
        "wineVersion": {"name": "Proton-EM Latest", "type": "proton"},
        "useGameMode": False,
    }
    if section is not None:
        body.update(section)
    path.write_text(json.dumps({APP_NAME: body, "version": "v0"}, indent=2), encoding="utf-8")
    return path


def _archive(path: Path) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("OptiScaler_0.7.7/OptiScaler.dll", b"optiscaler proxy")
        archive.writestr("OptiScaler_0.7.7/OptiScaler.ini", b"[OptiScaler]\nEnabled=true\n")
    return path


def _optiscaler(tmp_path: Path) -> OptiScalerService:
    return OptiScalerService(
        profile_repository=OptiScalerProfileRepository(tmp_path / "config/games"),
        data_root=tmp_path / "data/games",
        executable_resolver=GameExecutableResolver(),
        process_detector=lambda _path: (),
    )


class _Detector:
    def __init__(self, gamemode: bool = True) -> None:
        self._gamemode = gamemode

    def detect(self, *, refresh: bool = False):
        return (
            RuntimeToolAvailability("GameMode", self._gamemode, "/usr/bin/gamemoderun"),
            RuntimeToolAvailability("Gamescope", False),
        )


def _controller(tmp_path: Path, game: Game, *, optiscaler: OptiScalerService | None = None,
                gamemode: bool = False) -> AppController:
    profiles = GameOptimizationProfileRepository(tmp_path / "profiles")
    profiles.save(replace(GameOptimizationProfile.default(game.id), gamemode_enabled=gamemode))
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=profiles,
        mangohud_repository=MangoHudProfileRepository(tmp_path / "mango"),
        proton_tweaks_repository=ProtonTweaksRepository(tmp_path / "proton"),
        runtime_tool_detector=_Detector(),
        optiscaler_service=optiscaler or _optiscaler(tmp_path),
        launcher_integration=LauncherIntegrationService(
            tmp_path / "state", lambda *_a, **_k: LauncherPlan(), home=tmp_path,
            process_commands=lambda: (),
        ),
        initial_games=(game,),
        auto_refresh=False,
    )
    controller._launcher_integration = LauncherIntegrationService(
        tmp_path / "state", controller._optimization_controller.launcher_plan,
        home=tmp_path, process_commands=lambda: (),
    )
    return controller


# -- compatibility -------------------------------------------------------------


def test_old_cache_entries_keep_their_existing_keys(tmp_path: Path) -> None:
    """A cache written before profile_key/capabilities existed still loads and
    yields exactly the keys that existing profiles were saved under."""

    steam = Game(
        id="steam-224760", steam_app_id="224760", name="Steam", launcher=Launcher.STEAM,
        install_path=tmp_path / "s", logical_size_gb=0, physical_size_gb=0,
        filesystem=FilesystemType.EXT4, compression_available=False, data_source="Steam",
    )
    local = Game(
        id="local-0123456789abcdef01234567", name="Local", launcher=Launcher.MANUAL,
        install_path=tmp_path / "l", logical_size_gb=0, physical_size_gb=0,
        filesystem=FilesystemType.EXT4, compression_available=False, data_source="Local",
    )
    cache = LibraryCache(tmp_path / "library-v1.json")
    cache.save([steam, local, _heroic_game(tmp_path)])
    raw = json.loads(cache.path.read_text())
    assert "profile_key" not in json.dumps(raw) and "capabilities" not in json.dumps(raw)
    loaded = {game.id: game for game in cache.load()}
    assert loaded["steam-224760"].profile_key == "224760"
    assert loaded[local.id].profile_key == local.id
    assert loaded[GAME_ID].profile_key == GAME_ID
    # Existing Steam profiles are found under the unchanged key.
    repository = OptiScalerProfileRepository(tmp_path / "config/games")
    profile = repository.load("224760")
    repository.save(replace(profile, executable="Binaries/Win64/Game.exe"))
    assert OptiScalerService.game_key(loaded["steam-224760"]) == "224760"
    assert repository.load(OptiScalerService.game_key(loaded["steam-224760"])).executable == (
        "Binaries/Win64/Game.exe"
    )


def test_narrator_key_for_heroic_is_unchanged() -> None:
    """Narrator settings are keyed separately; profile_key must not move them."""
    from game_optimization_linux.controllers.narrator_controller import NarratorController

    game = Game(
        id=GAME_ID, name="x", launcher=Launcher.HEROIC, install_path=Path("/x"),
        logical_size_gb=0, physical_size_gb=0, filesystem=FilesystemType.EXT4,
        compression_available=False,
    )
    assert NarratorController._game_key(game).startswith("local-")


# -- capabilities --------------------------------------------------------------


def test_capabilities_come_from_actual_heroic_data(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    missing = game_capabilities(game, launcher_facts=launcher_facts(game, tmp_path))
    assert missing["optiscaler"]["available"] is False
    assert "Heroic" in missing["optiscaler"]["reason"]
    assert missing["optimization"]["available"] is False and missing["optimization"]["reason"]

    _heroic_config(tmp_path)
    caps = game_capabilities(
        game, launcher_facts=launcher_facts(game, tmp_path), launcher_writable=(True, ""),
    )
    assert caps["gameDirectory"]["available"] and caps["executable"]["available"]
    assert caps["runner"]["available"] and caps["launcherConfig"]["available"]
    assert caps["optiscaler"]["available"] and caps["optimization"]["available"]

    read_only = game_capabilities(
        game, launcher_facts=launcher_facts(game, tmp_path),
        launcher_writable=(False, "GameOpti cannot write the Heroic settings: read-only"),
    )
    assert read_only["optiscaler"]["available"] is False
    assert "read-only" in read_only["optiscaler"]["reason"]

    gone = game_capabilities(replace(game, install_path=tmp_path / "nowhere"),
                             launcher_facts=launcher_facts(game, tmp_path),
                             launcher_writable=(True, ""))
    assert gone["optiscaler"] == {"available": False, "reason": "The game directory is unavailable"}


def test_capabilities_keep_steam_and_custom_behaviour(tmp_path: Path) -> None:
    steam = Game(
        id="steam-1", steam_app_id="1", name="S", launcher=Launcher.STEAM,
        install_path=tmp_path, logical_size_gb=0, physical_size_gb=0,
        filesystem=FilesystemType.EXT4, compression_available=False, data_source="Steam",
    )
    assert game_capabilities(steam)["optiscaler"]["available"] is True
    custom = replace(steam, id="manual-1", steam_app_id=None, launcher=Launcher.MANUAL, data_source="Manual")
    caps = game_capabilities(custom)
    assert caps["optimization"]["available"] is False and "custom" in caps["optimization"]["reason"]


# -- OptiScaler: explicit executable -------------------------------------------


def test_optiscaler_key_for_heroic_is_the_stable_game_id(tmp_path: Path) -> None:
    assert OptiScalerService.game_key(_heroic_game(tmp_path)) == GAME_ID


def test_ambiguous_executable_blocks_install_until_explicit_choice(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    service = _optiscaler(tmp_path)
    archive = _archive(tmp_path / "OptiScaler_v0.7.7.zip")

    status = service.status(game)
    assert status["executableStatus"] == "ambiguous"
    assert status["selectedExecutable"] == {}
    candidates = status["executableCandidates"]
    # Project binary ranks above the top-level stub; Engine helpers are last.
    assert candidates[0]["relativePath"] == "SpellProject/Binaries/Win64/SpellGame.exe"
    assert candidates[1]["relativePath"] == "SpellGame.exe"
    assert candidates[0]["score"] > candidates[1]["score"]
    assert "game_binary_directory" in candidates[0]["reasons"]
    assert candidates[0]["fullPath"] == str(game.install_path / "SpellProject/Binaries/Win64/SpellGame.exe")
    # The launcher-reported stub is never taken as a confident choice.
    with pytest.raises(OptiScalerError):
        service.plan(game, archive)
    with pytest.raises(OptiScalerError):
        service.install(game, archive)
    assert not (game.install_path / "SpellProject/Binaries/Win64/OptiScaler.ini").exists()
    assert not (game.install_path / "OptiScaler.ini").exists()

    profile = service.remember_executable(game, "SpellProject/Binaries/Win64/SpellGame.exe")
    assert profile.app_id == GAME_ID
    assert service.status(game)["executableStatus"] == "selected"
    plan = service.plan(game, archive)
    assert Path(plan.install_directory) == game.install_path / "SpellProject/Binaries/Win64"


def test_heroic_optiscaler_install_restore_and_backup(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    service = _optiscaler(tmp_path)
    archive = _archive(tmp_path / "OptiScaler_v0.7.7.zip")
    service.remember_executable(game, "SpellProject/Binaries/Win64/SpellGame.exe")
    target = game.install_path / "SpellProject/Binaries/Win64"
    (target / "dxgi.dll").write_bytes(b"original")

    profile = service.install(game, archive, allow_replace_conflicts=True)
    assert profile.installation_state == "installed" and profile.app_id == GAME_ID
    assert (target / "dxgi.dll").read_bytes() == b"optiscaler proxy"
    assert (service.backup_root(GAME_ID, profile.manifest_id) / "dxgi.dll").read_bytes() == b"original"
    assert service.verify(game) is not None

    removed = service.remove(game)
    assert removed.installation_state == "removed"
    assert (target / "dxgi.dll").read_bytes() == b"original"
    assert not (target / "OptiScaler.ini").exists()


def test_controller_status_and_choice_for_heroic_game(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    _heroic_config(tmp_path)
    controller = _controller(tmp_path, game)
    try:
        status = controller._optiscaler_controller.getOptiScalerStatus(game.id)
        assert status["success"] is True
        assert status["executableStatus"] == "ambiguous"
        chosen = controller._optiscaler_controller.rememberOptiScalerExecutable(
            game.id, "SpellProject/Binaries/Win64/SpellGame.exe"
        )
        assert chosen["success"] is True
        caps = controller.getGameCapabilities(game.id)
        assert caps["optiscaler"]["available"] is True
    finally:
        controller.shutdown()


def test_installed_optiscaler_reaches_heroic_through_dll_override(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    config = _heroic_config(tmp_path)
    service = _optiscaler(tmp_path)
    service.remember_executable(game, "SpellProject/Binaries/Win64/SpellGame.exe")
    service.install(game, _archive(tmp_path / "OptiScaler_v0.7.7.zip"))
    controller = _controller(tmp_path, game, optiscaler=service)
    try:
        status = controller.getLauncherIntegration(game.id)
        assert status["environment"].get("WINEDLLOVERRIDES") == "dxgi=n,b"
        assert any("WINEDLLOVERRIDES=dxgi=n,b" in line for line in status["changes"])
        assert status["canApply"] is True
        before = config.read_text()
        assert controller.applyLauncherIntegration(game.id)["state"] == "applied"
        env = {item["key"]: item["value"] for item in json.loads(config.read_text())[APP_NAME]["enviromentOptions"]}
        assert env == {"KEEP": "1", "WINEDLLOVERRIDES": "dxgi=n,b"}
        assert controller.restoreLauncherIntegration(game.id)["state"] == "not_applied"
        assert config.read_text() == before
    finally:
        controller.shutdown()


# -- launcher integration safety ------------------------------------------------


def _service(tmp_path: Path, plan: LauncherPlan) -> LauncherIntegrationService:
    return LauncherIntegrationService(
        tmp_path / "state", lambda *_a, **_k: plan, home=tmp_path, process_commands=lambda: (),
    )


def test_foreign_heroic_wrapper_is_refused_never_overwritten(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    config = _heroic_config(tmp_path, {"wrapperOptions": [{"exe": "user-wrapper", "args": "-x"}]})
    before = config.read_text()
    service = _service(tmp_path, LauncherPlan(prefix=("gamemoderun",), environment={}))
    status = service.status(game)
    assert status["canApply"] is False and "user-wrapper -x" in status["message"]
    with pytest.raises(LauncherSyncError, match="did not add"):
        service.apply(game)
    assert config.read_text() == before
    # Environment-only plans (OptiScaler) still work and keep the wrapper.
    env_only = _service(tmp_path, LauncherPlan(environment={"WINEDLLOVERRIDES": "dxgi=n,b"}))
    assert env_only.apply(game)["state"] == "applied"
    section = json.loads(config.read_text())[APP_NAME]
    assert section["wrapperOptions"] == [{"exe": "user-wrapper", "args": "-x"}]
    env_only.restore(game)
    assert config.read_text() == before


def test_existing_heroic_values_are_never_overwritten(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    config = _heroic_config(tmp_path, {"enviromentOptions": [
        {"key": "MANGOHUD", "value": "0"},
        {"key": "WINEDLLOVERRIDES", "value": "dxgi=b;mscoree=d"},
    ]})
    before = config.read_text()
    service = _service(tmp_path, LauncherPlan(environment={"MANGOHUD": "1", "WINEDLLOVERRIDES": "dxgi=n,b"}))
    status = service.status(game)
    assert status["canApply"] is False
    assert any("MANGOHUD=0" in line for line in status["conflicts"])
    assert any("dxgi=b" in line for line in status["conflicts"])
    with pytest.raises(LauncherSyncError):
        service.apply(game)
    assert config.read_text() == before
    # A compatible existing override is merged, not replaced.
    _heroic_config(tmp_path, {"enviromentOptions": [{"key": "WINEDLLOVERRIDES", "value": "mscoree=d"}]})
    merged = _service(tmp_path, LauncherPlan(environment={"WINEDLLOVERRIDES": "dxgi=n,b"}))
    merged.apply(game)
    env = {item["key"]: item["value"] for item in json.loads(config.read_text())[APP_NAME]["enviromentOptions"]}
    assert "mscoree=d" in env["WINEDLLOVERRIDES"] and "dxgi=n,b" in env["WINEDLLOVERRIDES"]


def test_heroic_builtin_tools_are_not_added_twice(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    _heroic_config(tmp_path, {"useGameMode": True, "showMangohud": True,
                              "gamescope": {"enableUpscaling": True}})
    facts = launcher_facts(game, tmp_path)
    assert dict(facts.builtins) == {"gamemode": True, "mangohud": True, "gamescope": True}
    controller = _controller(tmp_path, game, gamemode=True)
    try:
        plan = controller._optimization_controller.launcher_plan(game)
        assert plan.prefix == ()
        assert plan.features["gamemode"]["available"] is False
        assert "already enables GameMode" in plan.features["gamemode"]["reason"]
    finally:
        controller.shutdown()


def test_proton_tweaks_only_with_confirmed_proton_runner(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path)
    for kind, expected in (("proton", True), ("wine", False)):
        _heroic_config(tmp_path, {"wineVersion": {"name": "x", "type": kind}})
        controller = _controller(tmp_path / kind, game)
        controller._launcher_integration._home = tmp_path
        try:
            repository = controller._proton_tweaks_repository
            repository.save(repository.from_payload(game.id, {"enabledTweaks": ["proton_log"]}))
            plan = controller._optimization_controller.launcher_plan(game)
            assert ("PROTON_LOG" in plan.environment) is expected
            assert plan.features["proton"]["available"] is expected
        finally:
            controller.shutdown()


# -- GUI thread: the sandbox check never blocks ---------------------------------


class _SlowHost:
    def __init__(self) -> None:
        self.release = Event()
        self.calls = 0

    def flatpak_app_capabilities(self, app_id: str, commands):
        self.calls += 1
        self.release.wait(20.0)  # the real probe may take up to 20 s
        return {"commands": list(commands), "extensions": []}

    def flatpak_runtime(self, app_id: str) -> str:
        return "org.freedesktop.Platform/x86_64/25.08"


def test_page_status_does_not_wait_for_the_flatpak_sandbox_probe(tmp_path: Path) -> None:
    game = _heroic_game(tmp_path, variant="flatpak")
    flatpak_home = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig"
    flatpak_home.mkdir(parents=True)
    (flatpak_home / f"{APP_NAME}.json").write_text(json.dumps({APP_NAME: {
        "enviromentOptions": [], "wrapperOptions": [], "wineVersion": {"type": "proton"},
    }}), encoding="utf-8")
    controller = _controller(tmp_path, game, gamemode=True)
    host = _SlowHost()
    controller._host_service = host
    try:
        started = time.monotonic()
        status = controller.getLauncherIntegration(game.id)
        profile = controller.getOptimizationProfile(game.id)
        elapsed = time.monotonic() - started
        assert elapsed < 2.0
        assert status["pending"] is True and status["canApply"] is False
        assert "Checking the Heroic Flatpak sandbox" in status["message"]
        assert profile["success"] is True
        with pytest.raises(LauncherSyncError, match="Checking"):
            controller._launcher_integration.apply(game)
        host.release.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status = controller.getLauncherIntegration(game.id)
            if not status["pending"]:
                break
            time.sleep(0.05)
        assert status["pending"] is False and status["features"]["gamemode"]["available"] is True
        assert host.calls == 1
    finally:
        host.release.set()
        controller.shutdown()


# -- tool environments: host vs GameOpti vs the launcher's Flatpak ----------------


class _HeroicLikeHost(_SlowHost):
    """The real Heroic Flatpak (runtime 25.08) only ships gamemoderun."""

    def flatpak_app_capabilities(self, app_id: str, commands):
        self.calls += 1
        self.release.wait(20.0)
        return {"commands": [name for name in commands if name == "gamemoderun"], "extensions": []}


def _flatpak_heroic(tmp_path: Path) -> Game:
    game = _heroic_game(tmp_path, variant="flatpak")
    directory = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig"
    directory.mkdir(parents=True)
    (directory / f"{APP_NAME}.json").write_text(json.dumps({APP_NAME: {
        "enviromentOptions": [], "wrapperOptions": [], "wineVersion": {"type": "proton"},
    }}), encoding="utf-8")
    return game


def test_capabilities_use_the_launcher_sandbox_not_the_host(tmp_path: Path) -> None:
    game = _flatpak_heroic(tmp_path)
    controller = _controller(tmp_path, game)
    host = _HeroicLikeHost()
    controller._host_service = host
    try:
        started = time.monotonic()
        caps = controller.getGameCapabilities(game.id)
        assert time.monotonic() - started < 2.0  # never waits for the probe
        assert caps["pending"] is True
        assert caps["mangohud"]["available"] is False
        assert "Checking the Heroic Flatpak sandbox" in caps["mangohud"]["reason"]
        host.release.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and caps["pending"]:
            time.sleep(0.05)
            caps = controller.getGameCapabilities(game.id)
        tools = caps["tools"]
        assert tools["gamemoderun"]["available"] is True
        assert tools["mangohud"]["launcher"] is False and tools["gamescope"]["launcher"] is False
        assert caps["mangohud"] == {
            "available": False,
            "reason": "MangoHud is not available in the Heroic Flatpak environment (runtime 25.08)",
        }
        assert tools["gamescope"]["reason"] == (
            "Gamescope is not available in the Heroic Flatpak environment (runtime 25.08)"
        )
        assert tools["mangohud"]["launcherEnvironment"] == "Heroic Flatpak (runtime 25.08)"
        # GameMode is shipped by Heroic: Optimization stays available.
        assert caps["optimization"]["available"] is True
        assert host.calls == 1  # cached; one probe for all tools and the plan
        controller._optimization_controller.launcher_plan(game)
        assert host.calls == 1
    finally:
        host.release.set()
        controller.shutdown()
