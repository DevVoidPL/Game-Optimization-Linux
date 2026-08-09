from __future__ import annotations

import json
from pathlib import Path

import pytest

from game_optimization_linux.models import (
    AppSettings,
    FilesystemInfo,
    FilesystemType,
    Launcher,
)
from game_optimization_linux.providers.local import LocalGameProvider
from game_optimization_linux.providers import BtrfsCompressionProvider
from game_optimization_linux.services.library_cache import LibraryCache
from game_optimization_linux.services import OptiScalerProfileRepository, OptiScalerService
from game_optimization_linux.services import (
    GameOptimizationProfileRepository,
    MangoHudProfileRepository,
    ProtonTweaksRepository,
    RunnerIntegration,
)


class _Filesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path.parent,
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            writable=True,
            filesystem_name="btrfs",
            device="/dev/test",
        )


def _provider(root: Path, choices: Path) -> LocalGameProvider:
    return LocalGameProvider(_Filesystem(), (root,), choices_path=choices)  # type: ignore[arg-type]


def test_local_scan_is_limited_to_immediate_children_and_ignores_tools(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Configured Games"
    game = library / "Native Adventure"
    nested = game / "content"
    unrelated = tmp_path / "Outside Game"
    empty = library / "Documents"
    tool_only = library / "Redistributable"
    nested.mkdir(parents=True)
    unrelated.mkdir()
    empty.mkdir()
    tool_only.mkdir()
    executable = game / "Native Adventure"
    executable.write_bytes(b"native")
    executable.chmod(0o755)
    (nested / "support.exe").write_bytes(b"tool")
    (unrelated / "Outside Game.exe").write_bytes(b"outside")
    (tool_only / "setup.exe").write_bytes(b"setup")

    games = _provider(library, tmp_path / "choices.json").refresh()

    assert len(games) == 1
    assert games[0].name == "Native Adventure"
    assert games[0].launcher is Launcher.MANUAL
    assert games[0].steam_app_id is None
    assert games[0].data_source == "Local"
    assert games[0].source == "local"
    assert games[0].executable_path == "Native Adventure"
    assert games[0].filesystem is FilesystemType.BTRFS


def test_local_scan_keeps_native_game_when_auxiliary_exe_exists(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Games"
    game_root = library / "Hybrid Game"
    game_root.mkdir(parents=True)
    executable = game_root / "Hybrid Game"
    executable.write_bytes(b"native")
    executable.chmod(0o755)
    (game_root / "support.exe").write_bytes(b"tool")

    game = _provider(library, tmp_path / "choices.json").refresh()[0]

    assert game.executable_path == "Hybrid Game"
    assert game.executable_candidates == ("Hybrid Game",)


def test_ambiguous_local_executable_choice_survives_rescan_and_restart(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Games"
    game_root = library / "Mystery"
    game_root.mkdir(parents=True)
    (game_root / "alpha.exe").write_bytes(b"a")
    (game_root / "beta.exe").write_bytes(b"b")
    choices = tmp_path / "config" / "local-executables.json"

    provider = _provider(library, choices)
    first = provider.refresh()[0]
    assert first.executable_resolution == "ambiguous"
    assert first.executable_path == ""
    assert set(first.executable_candidates) == {"alpha.exe", "beta.exe"}

    selected = provider.select_executable(first.id, "beta.exe")
    assert selected.executable_path == "beta.exe"
    assert provider.refresh()[0].executable_path == "beta.exe"

    restarted = _provider(library, choices)
    restored = restarted.refresh()[0]
    assert restored.id == first.id
    assert restored.executable_path == "beta.exe"
    assert restored.executable_resolution == "selected"
    assert json.loads(choices.read_text(encoding="utf-8"))["choices"][first.id] == "beta.exe"


def test_local_game_cache_keeps_identity_without_inventing_steam_appid(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Games"
    root = library / "Windows Game"
    root.mkdir(parents=True)
    (root / "Windows Game.exe").write_bytes(b"exe")
    game = _provider(library, tmp_path / "choices.json").refresh()[0]
    cache = LibraryCache(tmp_path / "cache" / "library.json")

    cache.save((game,))
    restored = cache.load()[0]

    assert restored.id == game.id
    assert restored.steam_app_id is None
    assert restored.data_source == "Local"
    assert restored.executable_path == "Windows Game.exe"


def test_default_settings_do_not_scan_any_custom_directory() -> None:
    assert AppSettings().library_directories == ()


def test_configured_local_btrfs_game_passes_existing_path_safety_checks(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Games"
    root = library / "Btrfs Fixture"
    root.mkdir(parents=True)
    (root / "Btrfs Fixture.exe").write_bytes(b"exe")
    game = _provider(library, tmp_path / "choices.json").refresh()[0]
    provider = BtrfsCompressionProvider(executable_finder=lambda name: f"/bin/{name}")
    blockers: list[str] = []

    identity = provider._validate_game_root(game, blockers)

    assert identity is not None
    assert identity.app_id == game.id
    assert blockers == []


def test_local_game_uses_its_stable_id_for_optiscaler_profile(tmp_path: Path) -> None:
    library = tmp_path / "Games"
    root = library / "Windows Fixture"
    root.mkdir(parents=True)
    (root / "Windows Fixture.exe").write_bytes(b"exe")
    game = _provider(library, tmp_path / "choices.json").refresh()[0]
    repository = OptiScalerProfileRepository(tmp_path / "profiles")
    service = OptiScalerService(
        profile_repository=repository,
        data_root=tmp_path / "data",
        process_detector=lambda _path: (),
    )

    profile = service.remember_executable(game, "Windows Fixture.exe")
    status = service.status(game)

    assert profile.app_id == game.id
    assert repository.path(game.id).is_file()
    assert status["success"] is True
    assert status["appId"] == game.id
    assert status["selectedExecutable"]["relativePath"] == "Windows Fixture.exe"


def test_local_and_steam_runtime_profiles_use_separate_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    optimization = GameOptimizationProfileRepository(root)
    mangohud = MangoHudProfileRepository(root, log_root=tmp_path / "logs")
    proton = ProtonTweaksRepository(root)
    local_id = "local-0123456789abcdef01234567"

    optimization.save(optimization.default(local_id))
    mangohud.save(mangohud.default(local_id))
    proton.save(proton.load(local_id))

    assert optimization.path(local_id).parent == root / local_id
    assert mangohud.profile_path(local_id).parent == root / local_id
    assert proton.path(local_id).parent == root / local_id
    assert optimization.path("42").parent == root / "42"
    assert optimization.path(local_id) != optimization.path("42")


def test_native_local_game_launches_through_existing_runner(tmp_path: Path) -> None:
    library = tmp_path / "Games"
    game_root = library / "Native Fixture"
    game_root.mkdir(parents=True)
    executable = game_root / "Native Fixture"
    executable.write_bytes(b"native")
    executable.chmod(0o755)
    game = _provider(library, tmp_path / "choices.json").refresh()[0]
    runner = tmp_path / "game-optimization-run"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(argv: list[str], **kwargs: object) -> object:
        calls.append((argv, dict(kwargs)))
        return object()

    integration = RunnerIntegration(path=runner, popen=popen)

    argv = integration.launch_local(game)

    assert argv == (
        str(runner), "--appid", game.id, "--", str(executable),
    )
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == str(game_root)


def test_local_provider_deduplicates_configured_roots(tmp_path: Path) -> None:
    library = tmp_path / "Games"
    library.mkdir()
    provider = LocalGameProvider(
        _Filesystem(),
        (library, library / "."),
        choices_path=tmp_path / "choices.json",
    )  # type: ignore[arg-type]
    assert provider.roots == (library.resolve(),)


def test_nonexistent_local_library_is_rejected_by_settings_controller(
    tmp_path: Path,
) -> None:
    from game_optimization_linux.controllers.settings_controller import SettingsController

    class _App:
        _settings_model = AppSettings()

        @staticmethod
        def _coerce_bool(value):
            return bool(value)

        @staticmethod
        def _coerce_enum(_kind, value):
            return value

    controller = SettingsController(_App())  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        controller._coerce_setting_value(
            (),
            [str(tmp_path / "missing")],
            field_name="library_directories",
        )
