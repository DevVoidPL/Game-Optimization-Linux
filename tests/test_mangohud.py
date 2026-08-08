from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from gameforge.controllers.app_controller import AppController
from gameforge.models import FilesystemType, Game, GameStatus, Launcher, MangoHudProfile
from gameforge.providers import DemoSystemProvider
from gameforge.services import (
    KNOWN_CONFIG_KEYS,
    GameExecutableResolver,
    MangoHudAvailability,
    MangoHudConfigWriter,
    MangoHudDetector,
    MangoHudLaunchActivation,
    MangoHudLaunchIntegration,
    MangoHudProfileRepository,
    MockTaskService,
    SettingsStore,
    SteamLauncher,
    SteamLaunchError,
    build_steam_launch_plan,
)
from gameforge.services import mangohud as mangohud_module


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


def _game(
    path: Path, *, app_id: str = "242550", source: str = "Steam"
) -> Game:
    path.mkdir(parents=True, exist_ok=True)
    return Game(
        id=f"steam-{app_id}",
        name="Rayman Legends",
        launcher=Launcher.STEAM,
        install_path=path,
        library_path=path.parent,
        logical_size_gb=8.0,
        physical_size_gb=8.0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        steam_app_id=app_id,
        data_source=source,
        status=GameStatus.READY,
    )


class _Provider:
    def __init__(self, game: Game) -> None:
        self.game = game
        self.last_report = SimpleNamespace(steam_found=True)

    def list_games(self) -> tuple[Game, ...]:
        return (self.game,)

    def get_game(self, game_id: str) -> Game | None:
        return self.game if game_id == self.game.id else None

    def add_game(self, game: Game) -> Game:
        self.game = game
        return game

    def refresh(self) -> tuple[Game, ...]:
        return self.list_games()


class _Detector:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def detect(self, steam_type: str = "native") -> MangoHudAvailability:
        return MangoHudAvailability(
            available=self.available,
            steam_type=steam_type,
            version="v0.8.4",
            command_path="/usr/bin/mangohud" if self.available else "",
            layer_available=self.available,
            flatpak_layer_available=self.available and steam_type == "flatpak",
            supported_keys=tuple(sorted(KNOWN_CONFIG_KEYS)),
            message=(
                "MangoHud detected"
                if self.available
                else "MangoHud profile unavailable for this Steam installation"
            ),
        )


def test_profile_repository_is_per_appid_and_survives_restart(tmp_path: Path) -> None:
    repository = MangoHudProfileRepository(
        tmp_path / "config" / "games", log_root=tmp_path / "state" / "logs"
    )
    first = repository.default("242550").apply_preset("basic")
    second = repository.default("204360").apply_preset("fps_only")

    repository.save(first)
    repository.save(second)
    restarted = MangoHudProfileRepository(
        tmp_path / "config" / "games", log_root=tmp_path / "state" / "logs"
    )

    assert restarted.load("242550") == first
    assert restarted.load("204360") == second
    assert restarted.profile_path("242550") != restarted.profile_path("204360")


def test_repository_uses_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    calls: list[tuple[Path, Path]] = []
    original = mangohud_module.os.replace

    def replace_spy(source: object, target: object) -> None:
        calls.append((Path(source), Path(target)))
        original(source, target)

    monkeypatch.setattr(mangohud_module.os, "replace", replace_spy)
    path = repository.save(repository.default("242550").apply_preset("basic"))

    assert calls and calls[-1][1] == path
    assert not list(path.parent.glob("*.tmp"))


def test_config_generation_is_stable_allowlisted_and_has_no_duplicates() -> None:
    profile = MangoHudProfile.default("242550").apply_preset("extended")
    writer = MangoHudConfigWriter()

    first = writer.render(profile)
    second = writer.render(profile)
    keys = [
        line.split("=", 1)[0]
        for line in first.splitlines()
        if line and not line.startswith("#")
    ]

    assert first == second
    assert len(keys) == len(set(keys))
    assert "# Steam AppID: 242550" in first
    assert "gpu_stats" in first
    assert "wine" in first
    assert "permit_upload=0" in first


def test_fps_only_explicitly_disables_unselected_default_metrics() -> None:
    content = MangoHudConfigWriter().render(
        MangoHudProfile.default("242550").apply_preset("fps_only")
    )

    assert "fps\n" in content
    assert "frametime\n" in content
    assert "gpu_stats=0" in content
    assert "cpu_stats=0" in content
    assert "frame_timing=0" in content


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"position": "center"}, "position"),
        ({"font_size": 200}, "font_size"),
        ({"background_alpha": 1.5}, "background_alpha"),
        ({"fps_limit": -2}, "fps_limit"),
        ({"toggle_hud_key": "F12\nexec=x"}, "key binding"),
    ],
)
def test_profile_validation_rejects_invalid_or_injectable_values(
    changes: dict[str, object], message: str
) -> None:
    values = MangoHudProfile.default("242550").to_dict()
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        MangoHudProfile.from_dict(values)


def test_custom_metrics_are_ordered_and_unknown_metrics_are_rejected() -> None:
    values = MangoHudProfile.default("242550").to_dict()
    values.update({"preset": "custom", "metrics": {"ram": True, "fps": True}})
    profile = MangoHudProfile.from_dict(values)
    assert profile.metrics == ("fps", "ram")

    values["metrics"] = ["fps", "shell_command"]
    with pytest.raises(ValueError, match="unsupported MangoHud metrics"):
        MangoHudProfile.from_dict(values)


def test_integral_qml_numbers_are_accepted_but_fractional_integers_are_not() -> None:
    values = MangoHudProfile.default("242550").to_dict()
    values.update({"font_size": 32.0, "round_corners": 8.0, "table_columns": 3.0})

    profile = MangoHudProfile.from_dict(values)

    assert profile.font_size == 32
    assert profile.round_corners == 8
    assert profile.table_columns == 3
    values["font_size"] = 32.5
    with pytest.raises(ValueError, match="font_size must be an integer"):
        MangoHudProfile.from_dict(values)


def test_reset_removes_only_gameforge_profile_and_config(tmp_path: Path) -> None:
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    profile = repository.default("242550").apply_preset("basic")
    repository.save(profile)
    MangoHudConfigWriter().write(profile, repository.config_path("242550"))
    unrelated = tmp_path / "MangoHud.conf"
    unrelated.write_text("user config", encoding="utf-8")

    reset = repository.reset("242550")

    assert reset.enabled is False
    assert not repository.profile_path("242550").exists()
    assert not repository.config_path("242550").exists()
    assert unrelated.read_text(encoding="utf-8") == "user config"


def test_reset_removes_only_gameforge_flatpak_mirror(tmp_path: Path) -> None:
    game = _game(tmp_path / "game", source="Steam Flatpak")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    integration = MangoHudLaunchIntegration(
        repository,
        _Detector(),  # type: ignore[arg-type]
        flatpak_config_root=tmp_path / "flatpak-config",
    )
    mirror = tmp_path / "flatpak-config" / "242550" / "MangoHud.conf"
    mirror.parent.mkdir(parents=True)
    mirror.write_text("fps\n", encoding="utf-8")
    unrelated = tmp_path / "flatpak-config" / "unrelated.conf"
    unrelated.write_text("user data", encoding="utf-8")

    integration.reset(game)

    assert not mirror.exists()
    assert unrelated.read_text(encoding="utf-8") == "user data"


def test_detector_reports_missing_mangohud_without_installing_anything(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    detector = MangoHudDetector(
        which=lambda _name: None,
        command_runner=runner,
        example_paths=(tmp_path / "missing",),
        layer_paths=(tmp_path / "missing-layer",),
    )

    status = detector.detect("native")

    assert status.available is False
    assert "not installed" in status.message
    assert calls == []


def test_detector_uses_host_service_path_probe_inside_flatpak() -> None:
    class Host:
        def tool_info(self, name: str) -> dict[str, object]:
            assert name == "mangohud"
            return {
                "available": True,
                "executable": "mangohud",
                "version": "v0.8.4",
                "source": "host",
            }

    detector = MangoHudDetector(
        which=lambda _name: None,
        example_paths=(),
        layer_paths=(),
        host_service=Host(),
    )
    status = detector.detect("native")
    assert status.available is True
    assert status.command_path == "mangohud"
    assert status.version == "v0.8.4"
    assert status.layer_available is True


def test_native_plan_contains_per_game_mangohud_environment(tmp_path: Path) -> None:
    game = _game(tmp_path / "Rayman Legends")
    config = tmp_path / "games" / "242550" / "MangoHud.conf"
    activation = MangoHudLaunchActivation(
        enabled=True,
        available=True,
        environment={"MANGOHUD": "1", "MANGOHUD_CONFIGFILE": str(config)},
        config_path=config,
        steam_type="native",
    )

    plan = build_steam_launch_plan(
        game, activation=activation, which=lambda name: "/usr/bin/steam" if name == "steam" else None
    )

    assert plan.command == ["/usr/bin/steam", "-applaunch", "242550"]
    assert plan.environment["MANGOHUD_CONFIGFILE"] == str(config)
    assert plan.mangohud_config_path == config


def test_flatpak_plan_passes_each_environment_value_as_one_argv_item(tmp_path: Path) -> None:
    game = _game(tmp_path / "House Flipper", source="Steam Flatpak")
    config = tmp_path / "Flatpak Config With Space" / "MangoHud.conf"
    activation = MangoHudLaunchActivation(
        enabled=True,
        available=True,
        environment={"MANGOHUD": "1", "MANGOHUD_CONFIGFILE": str(config)},
        config_path=config,
        steam_type="flatpak",
    )

    plan = build_steam_launch_plan(
        game,
        activation=activation,
        which=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
    )

    assert f"--env=MANGOHUD_CONFIGFILE={config}" in plan.arguments
    assert plan.arguments[-3:] == ("com.valvesoftware.Steam", "-applaunch", "242550")
    assert all("shell" not in argument for argument in plan.arguments)


def test_launcher_never_uses_shell_for_mangohud_plan(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    calls: list[dict[str, object]] = []
    launcher = SteamLauncher(
        which=lambda _name: "/usr/bin/steam",
        popen=lambda _argv, **kwargs: calls.append(kwargs),
        native_steam_environment=lambda: None,
    )
    activation = MangoHudLaunchActivation(
        enabled=True,
        available=True,
        environment={"MANGOHUD": "1", "MANGOHUD_CONFIGFILE": "/tmp/MangoHud.conf"},
        config_path=Path("/tmp/MangoHud.conf"),
        steam_type="native",
    )

    launcher.launch(game, activation)

    assert len(calls) == 1
    assert "shell" not in calls[0]
    assert calls[0]["env"]["MANGOHUD_CONFIGFILE"] == "/tmp/MangoHud.conf"  # type: ignore[index]


def test_running_native_steam_with_different_environment_blocks_false_success(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    calls: list[object] = []
    launcher = SteamLauncher(
        which=lambda _name: "/usr/bin/steam",
        popen=lambda *_args, **_kwargs: calls.append(object()),
        native_steam_environment=lambda: {
            "MANGOHUD": "1",
            "MANGOHUD_CONFIGFILE": "/home/user/.config/goverlay/MangoHud.conf",
        },
    )
    activation = MangoHudLaunchActivation(
        enabled=True,
        available=True,
        environment={
            "MANGOHUD": "1",
            "MANGOHUD_CONFIGFILE": "/home/user/.config/gameforge/242550/MangoHud.conf",
        },
        config_path=Path("/home/user/.config/gameforge/242550/MangoHud.conf"),
        steam_type="native",
    )

    with pytest.raises(SteamLaunchError, match="Steam is already running"):
        launcher.launch(game, activation)

    assert calls == []


def test_running_native_steam_with_matching_environment_can_launch(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    config = "/home/user/.config/gameforge/242550/MangoHud.conf"
    calls: list[object] = []
    launcher = SteamLauncher(
        which=lambda _name: "/usr/bin/steam",
        popen=lambda *_args, **_kwargs: calls.append(object()),
        native_steam_environment=lambda: {
            "MANGOHUD": "1",
            "MANGOHUD_CONFIGFILE": config,
        },
    )
    activation = MangoHudLaunchActivation(
        enabled=True,
        available=True,
        environment={"MANGOHUD": "1", "MANGOHUD_CONFIGFILE": config},
        config_path=Path(config),
        steam_type="native",
    )

    launcher.launch(game, activation)

    assert len(calls) == 1


def _controller(
    tmp_path: Path,
    game: Game,
    repository: MangoHudProfileRepository,
    detector: _Detector,
) -> AppController:
    integration = MangoHudLaunchIntegration(
        repository,
        detector,  # type: ignore[arg-type]
        flatpak_config_root=tmp_path / "flatpak-config",
        application_config_root=tmp_path / "MangoHud",
        native_steam_environment=lambda: None,
    )
    return AppController(
        game_provider=_Provider(game),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=DemoSystemProvider(),
        mangohud_repository=repository,
        mangohud_detector=detector,  # type: ignore[arg-type]
        mangohud_launch_integration=integration,
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )


def test_desktop_and_couch_payloads_save_the_same_profile_and_restart(tmp_path: Path) -> None:
    game = _game(tmp_path / "SteamLibrary" / "steamapps" / "common" / "Rayman Legends")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    controller = _controller(tmp_path, game, repository, _Detector())
    try:
        desktop = controller.saveMangoHudProfile(
            game.id,
            {
                "enabled": True,
                "preset": "basic",
                "position": "top-right",
                "fontSize": 28,
                "backgroundAlpha": 0.45,
                "roundCorners": 10,
                "compact": False,
                "horizontal": False,
                "tableColumns": 3,
                "fpsLimit": 60,
                "toggleHudKey": "Shift_R+F12",
                "metrics": [],
                "loggingEnabled": False,
                "logDuration": 60,
                "logInterval": 0.1,
                "outputFolder": str(tmp_path / "logs" / "242550"),
                "toggleLoggingKey": "Shift_L+F2",
            },
        )
        assert desktop["success"] is True
    finally:
        controller.shutdown()

    restarted = _controller(tmp_path, game, repository, _Detector())
    try:
        loaded = restarted.getMangoHudProfile(game.id)
        assert loaded["preset"] == "basic"
        assert loaded["position"] == "top-right"
        assert loaded["fpsLimit"] == 60

        couch = restarted.saveMangoHudProfile(
            game.id,
            {
                **loaded,
                "preset": "fps_only",
                "enabled": True,
                "fontSize": 32,
                "fpsLimit": 90,
            },
        )
        assert couch["success"] is True
        assert repository.load("242550").preset == "fps_only"
        assert repository.load("242550").font_size == 32
    finally:
        restarted.shutdown()


def _proton_game(tmp_path: Path, *, name: str = "Spelunky") -> Game:
    game = _game(tmp_path / name, app_id="239350")
    return replace(game, name=name)


def test_executable_resolver_prefers_game_and_ignores_tools(tmp_path: Path) -> None:
    game = _proton_game(tmp_path)
    for relative in (
        "Spelunky.exe",
        "uninstall.exe",
        "CrashReporter.exe",
        "redist/vcredist.exe",
        "tools/SpelunkyConfig.exe",
    ):
        path = game.install_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ")

    result = GameExecutableResolver().resolve(game)

    assert result.status == "confident"
    assert result.selected is not None
    assert result.selected.relative_path == "Spelunky.exe"
    assert all("uninstall" not in item.relative_path.casefold() for item in result.candidates)
    assert all("crash" not in item.relative_path.casefold() for item in result.candidates)


def test_executable_resolver_prefers_unreal_shipping_binary(tmp_path: Path) -> None:
    game = _proton_game(tmp_path, name="Example Game")
    launcher = game.install_path / "Launcher.exe"
    shipping = game.install_path / "ExampleGame/Binaries/Win64/ExampleGame-Win64-Shipping.exe"
    launcher.write_bytes(b"MZ")
    shipping.parent.mkdir(parents=True)
    shipping.write_bytes(b"MZ")

    result = GameExecutableResolver().resolve(game)

    assert result.selected is not None
    assert result.selected.relative_path.endswith("ExampleGame-Win64-Shipping.exe")


def test_ambiguous_executable_requires_and_remembers_manual_selection(
    tmp_path: Path,
) -> None:
    game = _proton_game(tmp_path, name="Unknown Title")
    (game.install_path / "alpha.exe").write_bytes(b"MZ")
    (game.install_path / "beta.exe").write_bytes(b"MZ")
    resolver = GameExecutableResolver()

    ambiguous = resolver.resolve(game)
    selected = resolver.resolve(game, "beta.exe")

    assert ambiguous.status == "ambiguous"
    assert ambiguous.selected is None
    assert selected.status == "selected"
    assert selected.selected is not None
    assert selected.selected.relative_path == "beta.exe"


def test_executable_path_validation_rejects_escape_from_game() -> None:
    values = MangoHudProfile.default("239350").to_dict()
    values["executable_path"] = "../outside.exe"

    with pytest.raises(ValueError, match="relative to the game"):
        MangoHudProfile.from_dict(values)


def _integration(
    tmp_path: Path,
    repository: MangoHudProfileRepository,
    steam_environment: dict[str, str] | None,
) -> MangoHudLaunchIntegration:
    return MangoHudLaunchIntegration(
        repository,
        _Detector(),  # type: ignore[arg-type]
        flatpak_config_root=tmp_path / "flatpak",
        application_config_root=tmp_path / "MangoHud",
        native_steam_environment=lambda: steam_environment,
    )


def test_active_mangohud_uses_wine_application_config_without_steam_restart(
    tmp_path: Path,
) -> None:
    game = _proton_game(tmp_path)
    (game.install_path / "Spelunky.exe").write_bytes(b"MZ")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    profile = replace(
        repository.default("239350").apply_preset("fps_only"),
        executable_path="Spelunky.exe",
    )
    repository.save(profile)
    integration = _integration(tmp_path, repository, {"MANGOHUD": "1"})

    status = integration.synchronize(game, profile)
    activation = integration.prepare(game, profile)
    target = tmp_path / "MangoHud" / "wine-Spelunky.conf"

    assert status.strategy == "per_application_config"
    assert status.requires_steam_restart is False
    assert activation.environment == {}
    assert activation.config_path == target
    assert target.is_file()
    assert target.read_text(encoding="utf-8").startswith(
        "# Managed by GameForge Linux\n# Steam AppID: 239350\n"
    )


def test_native_application_config_keeps_full_executable_name(tmp_path: Path) -> None:
    game = replace(_game(tmp_path / "Native Game", app_id="12345"), name="Native Game")
    executable = game.install_path / "NativeGame.x86_64"
    executable.write_bytes(b"ELF")
    executable.chmod(0o700)
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    profile = replace(
        repository.default("12345").apply_preset("basic"),
        executable_path="NativeGame.x86_64",
    )
    integration = _integration(tmp_path, repository, {"MANGOHUD": "1"})

    status = integration.synchronize(game, profile)

    assert status.application_config_path == (
        tmp_path / "MangoHud" / "NativeGame.x86_64.conf"
    )
    assert status.application_config_path.is_file()


def test_foreign_application_config_is_never_overwritten(tmp_path: Path) -> None:
    game = _proton_game(tmp_path)
    (game.install_path / "Spelunky.exe").write_bytes(b"MZ")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    profile = replace(
        repository.default("239350").apply_preset("basic"),
        executable_path="Spelunky.exe",
    )
    target = tmp_path / "MangoHud" / "wine-Spelunky.conf"
    target.parent.mkdir(parents=True)
    target.write_text("# Written by GOverlay\nfps\n", encoding="utf-8")
    integration = _integration(tmp_path, repository, {"MANGOHUD": "1"})

    status = integration.synchronize(game, profile)
    activation = integration.prepare(game, profile)

    assert status.status == "application_config_conflict"
    assert status.conflict_path == target
    assert activation.strategy == "steam_environment"
    assert activation.requires_steam_restart is True
    assert target.read_text(encoding="utf-8") == "# Written by GOverlay\nfps\n"


def test_explicit_goverlay_environment_forces_safe_steam_fallback(tmp_path: Path) -> None:
    game = _proton_game(tmp_path)
    (game.install_path / "Spelunky.exe").write_bytes(b"MZ")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    profile = replace(
        repository.default("239350").apply_preset("basic"),
        executable_path="Spelunky.exe",
    )
    repository.save(profile)
    integration = _integration(
        tmp_path,
        repository,
        {
            "MANGOHUD": "1",
            "MANGOHUD_CONFIGFILE": "/home/user/.local/share/goverlay/Spelunky.conf",
        },
    )

    activation = integration.prepare(game, profile)

    assert activation.strategy == "steam_environment"
    assert activation.strategy_status == "steam_config_override"
    assert activation.requires_steam_restart is True
    assert activation.environment["MANGOHUD_CONFIGFILE"] == str(
        repository.config_path("239350")
    )


def test_controller_persists_confident_executable_per_appid(tmp_path: Path) -> None:
    game = _proton_game(tmp_path)
    (game.install_path / "Spelunky.exe").write_bytes(b"MZ")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    controller = _controller(tmp_path, game, repository, _Detector())
    try:
        result = controller.saveMangoHudProfile(
            game.id, {"enabled": True, "preset": "fps_only"}
        )
    finally:
        controller.shutdown()

    assert result["success"] is True
    assert result["selectedExecutable"] == "Spelunky.exe"
    assert repository.load("239350").executable_path == "Spelunky.exe"


def test_controller_refuses_enabled_profile_when_installation_is_unavailable(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    controller = _controller(tmp_path, game, repository, _Detector(available=False))
    try:
        result = controller.saveMangoHudProfile(
            game.id, {"enabled": True, "preset": "basic"}
        )
        assert result["success"] is False
        assert result["activationEnabled"] is False
        assert repository.load("242550").enabled is False
    finally:
        controller.shutdown()
