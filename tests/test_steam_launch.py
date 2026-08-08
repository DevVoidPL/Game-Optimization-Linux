from __future__ import annotations

from pathlib import Path

import pytest

from gameforge.models import FilesystemType, Game, Launcher
from gameforge.services import (
    SteamLaunchError,
    SteamLauncher,
    build_steam_launch_command,
)


def _game(path: Path, *, source: str = "Steam", app_id: str = "4242") -> Game:
    path.mkdir(parents=True, exist_ok=True)
    return Game(
        id=f"steam-{app_id}",
        name="Launch Test",
        launcher=Launcher.STEAM,
        install_path=path,
        logical_size_gb=1.0,
        physical_size_gb=1.0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        steam_app_id=app_id,
        data_source=source,
    )


def test_builds_native_steam_launch_command(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")

    command = build_steam_launch_command(
        game, which=lambda name: "/usr/bin/steam" if name == "steam" else None
    )

    assert command == ["/usr/bin/steam", "-applaunch", "4242"]


def test_builds_flatpak_steam_launch_command(tmp_path: Path) -> None:
    game = _game(tmp_path / "game", source="Steam Flatpak")

    command = build_steam_launch_command(
        game, which=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None
    )

    assert command == [
        "/usr/bin/flatpak",
        "run",
        "com.valvesoftware.Steam",
        "-applaunch",
        "4242",
    ]


def test_launcher_uses_mocked_popen_without_shell(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(arguments: list[str], **kwargs: object) -> object:
        calls.append((arguments, kwargs))
        return object()

    launcher = SteamLauncher(which=lambda _name: "/usr/bin/steam", popen=popen)

    assert launcher.launch(game) == ["/usr/bin/steam", "-applaunch", "4242"]
    assert len(calls) == 1
    assert "shell" not in calls[0][1]
    assert calls[0][1]["start_new_session"] is True


def test_flatpak_launcher_resolves_steam_on_host_path_and_uses_spawn(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Host:
        def tool_info(self, name: str) -> dict[str, object]:
            assert name == "steam"
            return {
                "available": True,
                "native_available": True,
                "flatpak_available": False,
                "executable": "steam",
            }

    def popen(arguments: list[str], **kwargs: object) -> object:
        calls.append((arguments, kwargs))
        return object()

    launcher = SteamLauncher(
        which=lambda name: "/app/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
        popen=popen,
        host_service=Host(),
        environment={"FLATPAK_ID": "io.github.gameforge_linux.GameForge"},
    )
    command = launcher.launch(game)
    assert command == [
        "/app/bin/flatpak-spawn", "--host", "steam", "-applaunch", "4242"
    ]
    assert calls[0][0] == command
    assert "shell" not in calls[0][1]


def test_launch_preconditions_have_specific_errors(tmp_path: Path) -> None:
    missing_client = _game(tmp_path / "installed")
    with pytest.raises(SteamLaunchError, match="Steam executable not found"):
        build_steam_launch_command(missing_client, which=lambda _name: None)

    invalid_app_id = _game(tmp_path / "invalid", app_id="not-a-number")
    with pytest.raises(SteamLaunchError, match="Invalid Steam AppID"):
        build_steam_launch_command(invalid_app_id, which=lambda _name: "/usr/bin/steam")

    missing_directory = _game(tmp_path / "removed")
    missing_directory.install_path.rmdir()
    with pytest.raises(SteamLaunchError, match="installation directory not found"):
        build_steam_launch_command(missing_directory, which=lambda _name: "/usr/bin/steam")
