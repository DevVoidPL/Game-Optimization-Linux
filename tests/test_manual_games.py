from __future__ import annotations

import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace

import pytest

from game_optimization_linux.controllers.app_controller import AppController
from game_optimization_linux.models import (
    FilesystemInfo,
    FilesystemType,
    ManualGameConfig,
)
from game_optimization_linux.providers import (
    DemoGameProvider,
    DemoSystemProvider,
)
from game_optimization_linux.providers.local import (
    ConfiguredGameProvider,
    LocalGameProvider,
)
from game_optimization_linux.services import (
    ManualGameStore,
    MockTaskService,
    SettingsStore,
)
from game_optimization_linux.services.launcher_native import (
    ManualGameLauncher,
    ManualLaunchError,
)


class _Filesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=FilesystemType.EXT4,
            compression_supported=False,
            writable=True,
            filesystem_name="ext4",
            device="/dev/test",
        )


def _files(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "Game With Spaces"
    root.mkdir()
    executable = root / "game binary"
    executable.write_bytes(b"fixture")
    executable.chmod(0o755)
    return root, executable


def _configuration(
    tmp_path: Path,
    *,
    identifier: str = "manual-11111111-1111-4111-8111-111111111111",
    name: str = "Custom Game",
    **changes: object,
) -> ManualGameConfig:
    root, executable = _files(tmp_path)
    values: dict[str, object] = {
        "id": identifier,
        "name": name,
        "executable": executable,
        "install_directory": root,
    }
    values.update(changes)
    return ManualGameConfig(**values)  # type: ignore[arg-type]


def _provider(tmp_path: Path, store: ManualGameStore) -> LocalGameProvider:
    roots = tmp_path / "automatic"
    roots.mkdir(exist_ok=True)
    return LocalGameProvider(
        _Filesystem(),
        (roots,),
        choices_path=tmp_path / "choices.json",
        manual_store=store,
    )  # type: ignore[arg-type]


def test_manual_store_add_edit_remove_and_reload_atomically(tmp_path: Path) -> None:
    store = ManualGameStore(tmp_path / "config" / "manual-games-v1.json")
    game = _configuration(
        tmp_path,
        arguments=("--mode", "High quality"),
        environment=(("EMPTY", ""), ("UNICODE", "zażółć")),
    )

    store.upsert(game)
    loaded = ManualGameStore(store.path).load()[0]
    assert loaded == game
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 1

    edited = ManualGameConfig.from_dict({**game.to_dict(), "name": "Edited"})
    store.upsert(edited)
    assert store.load() == (edited,)
    assert edited.id == game.id
    assert store.remove(game.id) == edited
    assert store.load() == ()


def test_manual_provider_preserves_same_title_games_and_custom_artwork(
    tmp_path: Path,
) -> None:
    store = ManualGameStore(tmp_path / "manual.json")
    first_root = tmp_path / "first"
    first_root.mkdir()
    first_exe = first_root / "game"
    first_exe.write_bytes(b"one")
    first_exe.chmod(0o755)
    second_root = tmp_path / "second"
    second_root.mkdir()
    second_exe = second_root / "game"
    second_exe.write_bytes(b"two")
    second_exe.chmod(0o755)
    portrait = tmp_path / "portrait.png"
    header = tmp_path / "header.png"
    portrait.write_bytes(b"portrait")
    header.write_bytes(b"header")
    first = ManualGameConfig(
        id="manual-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        name="Same title",
        executable=first_exe,
        install_directory=first_root,
        portrait_artwork=portrait,
        header_artwork=header,
    )
    second = ManualGameConfig(
        id="manual-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        name="Same title",
        executable=second_exe,
        install_directory=second_root,
    )
    store.save((first, second))

    games = _provider(tmp_path, store).refresh()
    assert {game.id for game in games} == {first.id, second.id}
    presented = next(game for game in games if game.id == first.id)
    assert presented.data_source == "Manual"
    assert presented.portrait_artwork_path == portrait
    assert presented.header_artwork_path == header
    assert presented.launch_available is True


def test_missing_manual_paths_are_retained_with_precise_launch_diagnostic(
    tmp_path: Path,
) -> None:
    store = ManualGameStore(tmp_path / "manual.json")
    missing = ManualGameConfig(
        id="manual-cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        name="Disconnected",
        executable=tmp_path / "missing" / "game",
        install_directory=tmp_path / "missing",
    )
    store.save((missing,))
    game = _provider(tmp_path, store).refresh()[0]
    assert game.launch_available is False
    assert game.library_available is False
    assert "directory is missing" in game.launch_unavailable_reason


def test_qml_payload_uses_argv_and_structured_environment() -> None:
    config = ManualGameConfig.from_qml(
        {
            "name": "Quoted",
            "executable": "/games/Quoted/game.exe",
            "arguments": '--name "A value with spaces" --count 2',
            "environment": [
                {"key": "Z_VALUE", "value": "żółć"},
                {"key": "EMPTY", "value": ""},
            ],
            "runnerCommand": 'wine64 --option "one value"',
            "preLaunchCommand": "/usr/bin/pre --safe",
            "postLaunchCommand": "/usr/bin/post done",
        },
        identifier="manual-dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    )
    assert config.arguments == ("--name", "A value with spaces", "--count", "2")
    assert config.runner_command == ("wine64", "--option", "one value")
    assert config.environment == (("EMPTY", ""), ("Z_VALUE", "żółć"))
    assert config.install_directory == Path("/games/Quoted")
    assert config.pre_launch == ("/usr/bin/pre", "--safe")
    assert config.post_launch == ("/usr/bin/post", "done")


@pytest.mark.parametrize("key", ["BAD-NAME", "1START", "HAS SPACE", ""])
def test_invalid_environment_variable_name_is_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="invalid environment variable"):
        ManualGameConfig.from_qml(
            {
                "name": "Invalid",
                "executable": "/games/game",
                "environment": [{"key": key, "value": "secret"}],
            },
            identifier="manual-eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        )


def test_manual_launcher_runs_pre_game_post_without_shell_and_preserves_argv(
    tmp_path: Path,
) -> None:
    pre = tmp_path / "pre tool"
    post = tmp_path / "post tool"
    for path in (pre, post):
        path.write_bytes(b"tool")
        path.chmod(0o755)
    config = _configuration(
        tmp_path,
        arguments=("--title", "Value With Spaces"),
        environment=(("VISIBLE_NAME", "Unicode ✓"),),
        pre_launch=(str(pre), "prepare"),
        post_launch=(str(post), "cleanup"),
    )
    calls: list[tuple[str, list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append(("run", list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0)

    class _Process:
        def wait(self) -> int:
            return 0

    def popen(argv: list[str], **kwargs: object) -> object:
        calls.append(("popen", list(argv), dict(kwargs)))
        return _Process()

    launcher = ManualGameLauncher(popen=popen, run=run, environment={})
    result = launcher.launch(config)
    assert result.success is True
    assert [call[0] for call in calls] == ["run", "popen", "run"]
    assert calls[1][1][-2:] == ["--title", "Value With Spaces"]
    assert calls[1][2]["env"]["VISIBLE_NAME"] == "Unicode ✓"  # type: ignore[index]
    assert all(call[2].get("shell") is not True for call in calls)


def test_pre_launch_failure_stops_game_and_post_launch_is_not_run(tmp_path: Path) -> None:
    pre = tmp_path / "pre"
    pre.write_bytes(b"pre")
    pre.chmod(0o755)
    config = _configuration(tmp_path, pre_launch=(str(pre),))
    popen_calls: list[object] = []
    launcher = ManualGameLauncher(
        popen=lambda *_args, **_kwargs: popen_calls.append(object()),
        run=lambda *_args, **_kwargs: SimpleNamespace(returncode=9),
        environment={},
    )
    result = launcher.launch(config)
    assert result.game_exit_code is None
    assert result.pre_launch_exit_code == 9
    assert result.error == "Pre-launch command failed with status 9"
    assert popen_calls == []


def test_manual_launcher_validates_working_directory_and_wine_prefix(
    tmp_path: Path,
) -> None:
    config = _configuration(
        tmp_path,
        working_directory=tmp_path / "missing-workdir",
    )
    with pytest.raises(ManualLaunchError, match="working directory"):
        ManualGameLauncher(environment={}).prepare(config)


def test_manual_launcher_reports_missing_executable(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    config = ManualGameConfig(
        id="manual-ffffffff-ffff-4fff-8fff-ffffffffffff",
        name="Missing executable",
        executable=root / "missing",
        install_directory=root,
    )
    with pytest.raises(ManualLaunchError, match="executable is missing"):
        ManualGameLauncher(environment={}).prepare(config)


def test_flatpak_manual_launch_uses_host_argv_and_explicit_environment(
    tmp_path: Path,
) -> None:
    config = _configuration(
        tmp_path,
        arguments=("--path", "value with spaces"),
        environment=(("CUSTOM_VALUE", "zażółć"),),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    class _Process:
        def wait(self) -> int:
            return 0

    launcher = ManualGameLauncher(
        which=lambda name: "/app/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
        popen=lambda argv, **kwargs: calls.append((list(argv), dict(kwargs))) or _Process(),
        environment={"FLATPAK_ID": "io.github.DevVoidPL.GameOptimizationLinux"},
    )
    result = launcher.launch(config)
    assert result.success is True
    command = calls[0][0]
    assert command[:3] == [
        "/app/bin/flatpak-spawn",
        "--host",
        f"--directory={config.install_directory}",
    ]
    assert "--unset-env=FLATPAK_ID" in command
    assert "--env=CUSTOM_VALUE=zażółć" in command
    assert command[-3:] == [str(config.executable), "--path", "value with spaces"]
    assert calls[0][1]["cwd"] is None

    other = tmp_path / "other"
    other.mkdir()
    config = ManualGameConfig.from_dict(
        {
            **config.to_dict(),
            "working_directory": str(other),
            "wine_prefix": str(tmp_path / "missing-prefix"),
        }
    )
    with pytest.raises(ManualLaunchError, match="Wine prefix"):
        ManualGameLauncher(environment={}).prepare(config)


def test_optional_wine_command_and_prefix_build_expected_plan(tmp_path: Path) -> None:
    wine = tmp_path / "Wine With Spaces"
    wine.write_bytes(b"wine")
    wine.chmod(0o755)
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    config = _configuration(
        tmp_path,
        runner_command=(str(wine), "run"),
        wine_prefix=prefix,
        arguments=("--safe",),
    )
    plan = ManualGameLauncher(environment={}).prepare(config)
    assert plan.game_command[:2] == (str(wine), "run")
    assert plan.game_command[-1] == "--safe"
    assert dict(plan.environment)["WINEPREFIX"] == str(prefix)


def test_controller_add_edit_remove_keeps_stable_id_and_persistence(
    tmp_path: Path,
) -> None:
    root, executable = _files(tmp_path)
    store = ManualGameStore(tmp_path / "config" / "manual.json")
    local = _provider(tmp_path, store)
    provider = ConfiguredGameProvider(DemoGameProvider(()), local)
    controller = AppController(
        game_provider=provider,
        manual_game_store=store,
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=DemoSystemProvider(),
        initial_games=(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        created = controller.saveManualGame(
            {
                "name": "Controller Game",
                "executable": str(executable),
                "installDirectory": str(root),
                "arguments": '--label "space value"',
                "environment": [{"key": "LANGUAGE", "value": "pl_PL"}],
            }
        )
        assert created["success"] is True
        identifier = created["id"]
        assert identifier.startswith("manual-")
        assert controller.games[0]["customManualGame"] is True
        assert controller.games[0]["launcher"] == "Manual"

        edited = controller.saveManualGame(
            {
                **controller.manualGameConfig(identifier),
                "name": "Edited Controller Game",
            }
        )
        assert edited["id"] == identifier
        assert store.load()[0].name == "Edited Controller Game"
        assert controller.removeManualGame(identifier) is True
        assert store.load() == ()
        assert controller.games == []
        assert executable.is_file()
        assert root.is_dir()
    finally:
        controller.shutdown()


def test_controller_manual_launch_is_monitored_off_the_gui_thread(
    tmp_path: Path,
) -> None:
    root, executable = _files(tmp_path)
    store = ManualGameStore(tmp_path / "config" / "manual.json")
    local = _provider(tmp_path, store)
    provider = ConfiguredGameProvider(DemoGameProvider(()), local)
    release = Event()

    class _Launcher:
        @staticmethod
        def prepare(configuration: ManualGameConfig) -> object:
            return SimpleNamespace(
                game_id=configuration.id,
                game_command=(str(configuration.executable),),
                environment=(),
            )

        @staticmethod
        def execute(_plan: object) -> object:
            release.wait(2.0)
            return SimpleNamespace(error="", game_exit_code=0)

    controller = AppController(
        game_provider=provider,
        manual_game_store=store,
        manual_game_launcher=_Launcher(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=DemoSystemProvider(),
        initial_games=(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        created = controller.saveManualGame(
            {
                "name": "Async Game",
                "executable": str(executable),
                "installDirectory": str(root),
            }
        )
        started = time.monotonic()
        assert controller.launchGame(created["id"]) is True
        assert time.monotonic() - started < 0.2
        assert created["id"] in controller._manual_launch_jobs
        release.set()
        controller._manual_launch_jobs[created["id"]].result(timeout=2.0)
        controller._poll_manual_launch_jobs()
        assert controller._manual_launch_jobs == {}
    finally:
        release.set()
        controller.shutdown()


def test_manual_ui_exposes_editor_only_for_explicit_manual_games() -> None:
    root = Path("src/game_optimization_linux/qml")
    games_page = (root / "pages/GamesPage.qml").read_text(encoding="utf-8")
    details_page = (root / "pages/GameDetailsPage.qml").read_text(encoding="utf-8")
    dialog = (root / "dialogs/ManualGameDialog.qml").read_text(encoding="utf-8")
    assert 'objectName: "addManualGameButton"' in games_page
    assert "manualGameDialog.openForAdd()" in games_page
    assert "visible: page.customManualGame" in details_page
    assert "controller.removeManualGame" in details_page
    assert "FileDialog" in dialog and "FolderDialog" in dialog
    assert "controller.saveManualGame" in dialog
    assert "shell=True" not in dialog
