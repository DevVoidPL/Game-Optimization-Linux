from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gameforge.models.enums import (
    FilesystemType,
    GameStatus,
    SizeScanStatus,
)
from gameforge.models.game import Game
from gameforge.models.system import FilesystemInfo
from gameforge.providers.base import FilesystemProvider
from gameforge.providers.keyvalues import (
    VDFParseError,
    load_keyvalues,
    parse_keyvalues,
)
from gameforge.providers.steam import SteamGameProvider


GIB = 1024**3


class StubFilesystemProvider(FilesystemProvider):
    def __init__(self) -> None:
        self.inspected: list[Path] = []

    def inspect(self, path: Path) -> FilesystemInfo:
        self.inspected.append(path)
        return FilesystemInfo(
            mount_point=Path("/games"),
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            label="Games",
            device="/dev/test",
            mount_options=("rw", "compress=zstd"),
            writable=True,
            filesystem_name="btrfs",
        )

    def for_game(self, game: Game) -> FilesystemInfo:
        return self.inspect(game.install_path)

    def list_filesystems(self) -> tuple[FilesystemInfo, ...]:
        return ()


def _quoted(value: str | Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _manifest(
    app_id: str,
    name: str,
    install_dir: str,
    *,
    size: int = 0,
    state_flags: str = "4",
    last_updated: str | None = None,
    language: str | None = None,
) -> str:
    optional = ""
    if last_updated is not None:
        optional += f'\n    "LastUpdated" "{last_updated}"'
    if language is not None:
        optional += f'\n    "UserConfig" {{ "language" "{_quoted(language)}" }}'
    return f'''"AppState"
{{
    "appid" "{_quoted(app_id)}"
    "name" "{_quoted(name)}"
    "installdir" "{_quoted(install_dir)}"
    "SizeOnDisk" "{size}"
    "StateFlags" "{_quoted(state_flags)}"{optional}
}}
'''


def _write_manifest(
    library: Path,
    app_id: str,
    name: str,
    install_dir: str,
    *,
    create_directory: bool = True,
    **metadata: object,
) -> Path:
    steamapps = library / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    if create_directory:
        (steamapps / "common" / install_dir).mkdir(parents=True, exist_ok=True)
    path = steamapps / f"appmanifest_{app_id}.acf"
    path.write_text(
        _manifest(app_id, name, install_dir, **metadata),  # type: ignore[arg-type]
        encoding="utf-8",
    )
    return path


@pytest.fixture
def filesystem_provider() -> StubFilesystemProvider:
    return StubFilesystemProvider()


@pytest.fixture
def steam_tree(tmp_path: Path) -> dict[str, Path]:
    primary = tmp_path / "Steam Root"
    secondary = tmp_path / "Dysk z grami Łódź"
    empty = tmp_path / "Empty Library"
    for library in (primary, secondary, empty):
        (library / "steamapps" / "common").mkdir(parents=True)

    alias = tmp_path / "Secondary symlink"
    alias.symlink_to(secondary, target_is_directory=True)
    missing = tmp_path / "Disconnected disk"
    (primary / "steamapps" / "libraryfolders.vdf").write_text(
        f'''"libraryfolders"
{{
    "0" {{ "path" "{_quoted(primary)}" }}
    "1" {{ "path" "{_quoted(secondary)}" "apps" {{ "202" "1" }} }}
    "2" {{ "path" "{_quoted(alias)}" }}
    "3" {{ "path" "{_quoted(empty)}" }}
    "4" {{ "path" "{_quoted(missing)}" }}
}}
''',
        encoding="utf-8",
    )

    _write_manifest(
        primary,
        "101",
        "Portal Test",
        "Portal Test",
        size=2 * GIB,
        state_flags="4",
        last_updated="1700000000",
        language="polish",
    )
    _write_manifest(
        primary,
        "202",
        "Missing Example",
        "Missing Example",
        create_directory=False,
        size=512,
        state_flags="1026",
    )
    (primary / "steamapps" / "appmanifest_303.acf").write_text(
        '"AppState" { "appid" "303" "name" "Broken"', encoding="utf-8"
    )
    (primary / "steamapps" / "appmanifest_404.acf").write_text(
        _manifest("404", "Traversal", "../../outside"), encoding="utf-8"
    )
    _write_manifest(
        secondary,
        "101",
        "Duplicate Portal",
        "Duplicate Portal",
        size=3 * GIB,
    )
    _write_manifest(
        secondary,
        "505",
        "Żółta Gra",
        "Folder ze spacją",
        size=GIB,
    )
    return {
        "root": primary,
        "secondary": secondary,
        "empty": empty,
        "alias": alias,
        "missing": missing,
    }


def test_keyvalues_parser_handles_nested_comments_empty_and_escapes() -> None:
    parsed = parse_keyvalues(
        r'''
        // line comment
        "Root"
        {
            "empty" ""
            bare-key bare-value
            "quote" "say \"hello\""
            "windows" "D:\\Steam\\Games"
            /* block comment */
            "nested" { "unicode" "zażółć" }
        }
        '''
    )

    assert parsed == {
        "Root": {
            "empty": "",
            "bare-key": "bare-value",
            "quote": 'say "hello"',
            "windows": "D:\\Steam\\Games",
            "nested": {"unicode": "zażółć"},
        }
    }


def test_keyvalues_parser_accepts_empty_input_and_utf8_bom(tmp_path: Path) -> None:
    assert parse_keyvalues(" // nothing here\n") == {}
    path = tmp_path / "bom.vdf"
    path.write_text('\ufeff"key" "value"', encoding="utf-8")
    assert load_keyvalues(path) == {"key": "value"}


def test_keyvalues_parser_reports_excessive_nesting_as_parse_error() -> None:
    document = '"section" {' * 1_500 + '"key" "value"' + "}" * 1_500

    with pytest.raises(VDFParseError, match="nesting is too deep"):
        parse_keyvalues(document)


@pytest.mark.parametrize(
    "document",
    [
        '"root" { "key" "value"',
        '"root" "unterminated',
        '"key"',
        "}",
        '"root" { /* never closed',
    ],
)
def test_keyvalues_parser_reports_controlled_errors(document: str) -> None:
    with pytest.raises(VDFParseError) as error:
        parse_keyvalues(document)

    assert error.value.line >= 1
    assert error.value.column >= 1


def test_libraryfolders_supports_old_and_new_formats(
    tmp_path: Path, filesystem_provider: StubFilesystemProvider
) -> None:
    root = tmp_path / "Steam"
    old_library = tmp_path / "Old Format"
    for library in (root, old_library):
        (library / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'''"LibraryFolders"
{{
    "TimeNextStatsReport" "0"
    "1" "{_quoted(old_library)}"
}}
''',
        encoding="utf-8",
    )
    provider = SteamGameProvider(filesystem_provider, roots=[root])

    assert provider.discover_libraries() == (root.resolve(), old_library.resolve())
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'''"libraryfolders" {{ "1" {{ "path" "{_quoted(old_library)}" }} }}''',
        encoding="utf-8",
    )
    assert provider.discover_libraries() == (root.resolve(), old_library.resolve())


def test_damaged_library_entry_does_not_hide_valid_siblings(
    tmp_path: Path, filesystem_provider: StubFilesystemProvider
) -> None:
    root = tmp_path / "Steam"
    first = tmp_path / "First Library"
    second = tmp_path / "Second Library"
    for library in (root, first, second):
        (library / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'''"libraryfolders"
{{
    "1" {{ "path" "{_quoted(first)}" }}
    "2" {{ "path" "relative/broken" "dangling" }}
    "3" {{ "path" "{_quoted(second)}" }}
}}
''',
        encoding="utf-8",
    )
    provider = SteamGameProvider(filesystem_provider, roots=[root])

    assert provider.refresh() == ()
    assert provider.last_report.libraries == (
        root.resolve(),
        first.resolve(),
        second.resolve(),
    )
    assert provider.last_report.errors

def test_scan_detects_games_metadata_missing_files_and_filesystems(
    steam_tree: dict[str, Path], filesystem_provider: StubFilesystemProvider
) -> None:
    provider = SteamGameProvider(filesystem_provider, roots=[steam_tree["root"]])

    games = provider.refresh()
    by_id = {game.steam_app_id: game for game in games}

    assert set(by_id) == {"101", "202", "505"}
    portal = by_id["101"]
    assert portal.name == "Portal Test"
    assert portal.logical_size_gb == 2.0
    assert portal.physical_size_gb == 2.0
    assert portal.state_flags == 4
    assert portal.language == "polish"
    assert portal.last_updated_at == datetime.fromtimestamp(1700000000, UTC)
    assert portal.status is GameStatus.READY
    assert portal.filesystem is FilesystemType.BTRFS
    assert portal.compression_available
    assert portal.filesystem_device == "/dev/test"
    assert portal.mount_options == ("rw", "compress=zstd")
    assert portal.is_writable is True
    assert by_id["202"].status is GameStatus.MISSING_FILES
    assert by_id["505"].install_path.name == "Folder ze spacją"


def test_corrupt_and_traversal_manifests_do_not_stop_scan(
    steam_tree: dict[str, Path], filesystem_provider: StubFilesystemProvider
) -> None:
    provider = SteamGameProvider(filesystem_provider, roots=[steam_tree["root"]])

    games = provider.refresh()

    assert {game.steam_app_id for game in games} == {"101", "202", "505"}
    assert provider.last_report.manifests_seen == 6
    assert provider.last_report.invalid_manifests == 2
    assert provider.last_report.games_found == 3
    assert len(provider.last_report.errors) >= 2


def test_duplicate_libraries_symlinks_and_appids_are_eliminated(
    steam_tree: dict[str, Path], filesystem_provider: StubFilesystemProvider
) -> None:
    provider = SteamGameProvider(filesystem_provider, roots=[steam_tree["root"]])

    provider.refresh()

    report = provider.last_report
    assert report.libraries == (
        steam_tree["root"].resolve(),
        steam_tree["secondary"].resolve(),
        steam_tree["empty"].resolve(),
    )
    assert report.duplicate_libraries >= 2
    assert report.duplicate_games == 1
    assert [game.steam_app_id for game in provider.list_games()].count("101") == 1
    assert steam_tree["missing"].resolve() in report.inaccessible_paths
    assert steam_tree["missing"].resolve() in report.configured_libraries
    assert set(report.libraries).issubset(set(report.configured_libraries))


def test_empty_library_and_damaged_library_list_are_tolerated(
    tmp_path: Path, filesystem_provider: StubFilesystemProvider
) -> None:
    root = tmp_path / "empty"
    (root / "steamapps").mkdir(parents=True)
    library_file = root / "steamapps" / "libraryfolders.vdf"
    library_file.write_text('"libraryfolders" {', encoding="utf-8")
    provider = SteamGameProvider(filesystem_provider, roots=[root])

    assert provider.refresh() == ()
    assert provider.last_report.steam_found
    assert provider.last_report.libraries_found == 1
    assert provider.last_report.manifests_seen == 0
    assert provider.last_report.errors


def test_missing_steam_root_reports_not_found(
    tmp_path: Path, filesystem_provider: StubFilesystemProvider
) -> None:
    missing = tmp_path / "Steam is not installed here"
    provider = SteamGameProvider(filesystem_provider, roots=[missing])

    assert provider.refresh() == ()
    assert provider.steam_found is False
    assert provider.last_report.roots_found == 0
    assert provider.last_report.libraries_found == 0


def test_flatpak_source_is_inherited_by_external_library(
    tmp_path: Path, filesystem_provider: StubFilesystemProvider
) -> None:
    root = (
        tmp_path
        / ".var"
        / "app"
        / "com.valvesoftware.Steam"
        / "data"
        / "Steam"
    )
    _write_manifest(root, "777", "Flatpak Game", "Flatpak Game")
    provider = SteamGameProvider(filesystem_provider, roots=[root])

    game = provider.refresh()[0]

    assert game.data_source == "Steam Flatpak"


def test_explicit_roots_do_not_probe_standard_home(
    tmp_path: Path,
    filesystem_provider: StubFilesystemProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "only-this-root"
    (root / "steamapps").mkdir(parents=True)

    def forbidden_home() -> Path:
        raise AssertionError("Path.home must not be read with explicit roots")

    monkeypatch.setattr(Path, "home", forbidden_home)
    provider = SteamGameProvider(filesystem_provider, roots=[root])

    assert provider.refresh() == ()
    assert provider.last_report.roots_scanned == 1


def test_additional_roots_can_be_replaced_without_touching_disk(
    tmp_path: Path, filesystem_provider: StubFilesystemProvider
) -> None:
    provider = SteamGameProvider(
        filesystem_provider, roots=[], additional_roots=[tmp_path / "one"]
    )

    provider.set_additional_roots([tmp_path / "two", tmp_path / "two"])

    assert provider.configured_roots == ((tmp_path / "two").resolve(),)


def test_provider_can_apply_background_size_results_atomically(
    steam_tree: dict[str, Path], filesystem_provider: StubFilesystemProvider
) -> None:
    provider = SteamGameProvider(filesystem_provider, roots=[steam_tree["root"]])
    game = next(game for game in provider.refresh() if game.steam_app_id == "101")

    calculating = provider.mark_game_size_calculating(game.id)
    assert calculating is not None
    assert calculating.size_scan_status is SizeScanStatus.CALCULATING

    completed = provider.update_game_sizes(game.id, 2.5, 1.75)
    assert completed is not None
    assert completed.logical_size_gb == 2.5
    assert completed.physical_size_gb == 1.75
    assert completed.size_scan_status is SizeScanStatus.COMPLETED
    assert provider.get_game(game.id) == completed

    failed = provider.update_game_sizes(game.id, 99.0, 99.0, error=" vanished ")
    assert failed is not None
    assert failed.logical_size_gb == 99.0
    assert failed.physical_size_gb == 99.0
    assert failed.size_scan_status is SizeScanStatus.FAILED
    assert failed.size_scan_error == "vanished"
    assert provider.update_game_sizes("unknown", 0.0, 0.0) is None


def test_add_game_is_in_memory_and_survives_refresh(
    steam_tree: dict[str, Path], filesystem_provider: StubFilesystemProvider
) -> None:
    provider = SteamGameProvider(filesystem_provider, roots=[steam_tree["root"]])
    existing = provider.refresh()[0]
    manual = replace(
        existing,
        id="manual-record",
        steam_app_id=None,
        name="Manual Record",
        data_source="Manual",
    )

    assert provider.add_game(manual) == manual
    assert provider.get_game(manual.id) == manual
    assert manual in provider.refresh()
    with pytest.raises(ValueError):
        provider.add_game(manual)
