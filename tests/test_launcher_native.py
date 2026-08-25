from __future__ import annotations

from pathlib import Path

import pytest

from game_optimization_linux.controllers.app_controller import AppController
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.providers import DemoGameProvider, DemoSystemProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.launcher_native import (
    LauncherNativeError,
    LauncherNativeLauncher,
    build_launcher_native_command,
)


def _game(launcher: Launcher, *, variant: str, uri: str) -> Game:
    return Game(
        id=f"{launcher.value.casefold()}-id",
        name="Game With Spaces",
        launcher=launcher,
        launcher_game_id="id with spaces",
        install_path=Path("/games/Game With Spaces"),
        logical_size_gb=0,
        physical_size_gb=0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        launcher_variant=variant,
        launch_uri=uri,
    )


def test_heroic_native_and_flatpak_commands_preserve_uri_as_one_argument() -> None:
    native = _game(
        Launcher.HEROIC,
        variant="native",
        uri="heroic://launch?appName=id%20with%20spaces&runner=gog",
    )
    assert build_launcher_native_command(
        native, which=lambda name: "/usr/bin/heroic" if name == "heroic" else None, environment={}
    ) == [
        "/usr/bin/heroic", "--no-gui", "--no-sandbox",
        "heroic://launch?appName=id%20with%20spaces&runner=gog",
    ]

    flatpak = _game(Launcher.HEROIC, variant="flatpak", uri=native.launch_uri)
    assert build_launcher_native_command(
        flatpak, which=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None, environment={}
    ) == [
        "/usr/bin/flatpak", "run", "com.heroicgameslauncher.hgl", "--no-gui",
        "--no-sandbox", native.launch_uri,
    ]


def test_lutris_native_flatpak_and_sandbox_host_selection() -> None:
    native = _game(Launcher.LUTRIS, variant="native", uri="lutris:rungameid/12")
    assert build_launcher_native_command(
        native, which=lambda name: "/usr/bin/lutris" if name == "lutris" else None, environment={}
    ) == ["/usr/bin/lutris", "lutris:rungameid/12"]
    flatpak = _game(Launcher.LUTRIS, variant="flatpak", uri="lutris:rungameid/12")
    command = build_launcher_native_command(
        flatpak,
        which=lambda name: "/app/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
        environment={"FLATPAK_ID": "io.github.DevVoidPL.GameOptimizationLinux"},
    )
    assert command == [
        "/app/bin/flatpak-spawn", "--host", "--unset-env=FLATPAK_ID",
        "flatpak", "run", "net.lutris.Lutris",
        "lutris:rungameid/12",
    ]


def test_launcher_uses_popen_without_shell() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    game = _game(Launcher.LUTRIS, variant="native", uri="lutris:rungameid/12")
    launcher = LauncherNativeLauncher(
        which=lambda _name: "/usr/bin/lutris",
        popen=lambda argv, **kwargs: calls.append((argv, kwargs)),
        environment={},
    )
    launcher.launch(game)
    assert calls[0][0] == ["/usr/bin/lutris", "lutris:rungameid/12"]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["start_new_session"] is True


def test_unsupported_or_incomplete_launch_has_precise_diagnostic() -> None:
    game = _game(Launcher.HEROIC, variant="native", uri="")
    with pytest.raises(LauncherNativeError, match="incomplete"):
        build_launcher_native_command(game, environment={})


def test_controller_routes_only_heroic_and_lutris_to_launcher_native(
    tmp_path: Path,
) -> None:
    heroic = _game(
        Launcher.HEROIC,
        variant="flatpak",
        uri="heroic://launch?appName=id&runner=legendary",
    )
    steam = Game(
        id="steam-42",
        name="Steam Game",
        launcher=Launcher.STEAM,
        steam_app_id="42",
        install_path=tmp_path / "steam-game",
        logical_size_gb=0,
        physical_size_gb=0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
    )

    class RecordingLauncher:
        def __init__(self, command: tuple[str, ...]) -> None:
            self.command = command
            self.games: list[str] = []

        def launch(self, game: Game, *_args: object) -> tuple[str, ...]:
            self.games.append(game.id)
            return self.command

    native = RecordingLauncher(("flatpak", "run", "heroic"))
    steam_launcher = RecordingLauncher(("steam", "-applaunch", "42"))
    controller = AppController(
        game_provider=DemoGameProvider((heroic, steam)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=DemoSystemProvider(),
        game_launcher=steam_launcher,
        launcher_native=native,
        initial_games=(heroic, steam),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.launchGame(heroic.id) is True
        assert native.games == [heroic.id]
        assert steam_launcher.games == []

        assert controller.launchGame(steam.id) is True
        assert steam_launcher.games == [steam.id]
        assert native.games == [heroic.id]
    finally:
        controller.shutdown()
