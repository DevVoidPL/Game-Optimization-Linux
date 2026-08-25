from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import yaml

from game_optimization_linux.models import (
    FilesystemInfo,
    FilesystemType,
    GameStatus,
    Launcher,
)
from game_optimization_linux.providers.heroic import HeroicGameProvider, HeroicRoot
from game_optimization_linux.providers.lutris import LutrisGameProvider, LutrisRoot


class _Filesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path.anchor and Path(path.anchor) or Path("/"),
            filesystem=FilesystemType.EXT4,
            filesystem_name="ext4",
            compression_supported=False,
            writable=True,
        )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _heroic_epic(
    root: Path,
    install: Path,
    *,
    app_id: str = "epic-id",
    title: str = "Example Game",
) -> None:
    _write_json(
        root / "legendaryConfig" / "legendary" / "installed.json",
        {
            app_id: {
                "install_path": str(install),
                "executable": "Game.exe",
                "platform": "Windows",
            }
        },
    )
    _write_json(
        root / "store_cache" / "legendary_library.json",
        {
            "library": [
                {
                    "app_name": app_id,
                    "title": title,
                    "runner": "legendary",
                }
            ]
        },
    )


def test_heroic_native_epic_installed_game_and_local_artwork(tmp_path: Path) -> None:
    root = tmp_path / ".config" / "heroic"
    install = tmp_path / "Games" / "Game With Spaces"
    install.mkdir(parents=True)
    _heroic_epic(root, install)
    icon = root / "icons" / "epic-id.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"png")
    _write_json(
        root / "GamesConfig" / "epic-id.json",
        {"epic-id": {"winePrefix": str(tmp_path / "Prefixes" / "Game")}},
    )

    provider = HeroicGameProvider(
        _Filesystem(), roots=(HeroicRoot(root, "native"),)
    )
    game = provider.refresh()[0]

    assert game.id == "heroic-legendary-epic-id"
    assert game.launcher is Launcher.HEROIC
    assert game.launcher_game_id == "epic-id"
    assert game.store == "Epic"
    assert game.runner == "legendary"
    assert game.install_path == install
    assert game.executable_path == str(install / "Game.exe")
    assert game.wine_prefix == tmp_path / "Prefixes" / "Game"
    assert game.portrait_artwork_path == icon
    assert game.launch_uri == "heroic://launch?appName=epic-id&runner=legendary"
    assert provider.last_report.roots[0].state == "found"


def test_heroic_flatpak_gog_and_missing_install_directory(tmp_path: Path) -> None:
    root = tmp_path / ".var" / "app" / "com.heroicgameslauncher.hgl" / "config" / "heroic"
    root.mkdir(parents=True)
    missing = tmp_path / "external" / "missing-game"
    _write_json(
        root / "gog_store" / "installed.json",
        {
            "installed": [
                {
                    "appName": "gog-123",
                    "title": "GOG Game",
                    "install_path": str(missing),
                    "executable": "bin/game.exe",
                }
            ]
        },
    )

    game = HeroicGameProvider(
        _Filesystem(), roots=(HeroicRoot(root, "flatpak"),)
    ).refresh()[0]

    assert game.id == "heroic-gog-gog-123"
    assert game.store == "GOG"
    assert game.launcher_variant == "flatpak"
    assert game.status is GameStatus.MISSING_FILES
    assert game.library_available is False


def test_heroic_malformed_metadata_does_not_hide_valid_other_store(tmp_path: Path) -> None:
    root = tmp_path / "heroic"
    (root / "legendaryConfig" / "legendary").mkdir(parents=True)
    (root / "legendaryConfig" / "legendary" / "installed.json").write_text(
        "{broken", encoding="utf-8"
    )
    install = tmp_path / "Amazon Game"
    install.mkdir()
    _write_json(
        root / "nile_config" / "nile" / "installed.json",
        [{"id": "amazon-id", "title": "Amazon Game", "path": str(install)}],
    )

    provider = HeroicGameProvider(_Filesystem(), roots=(root, root))
    games = provider.refresh()

    assert [(game.id, game.store) for game in games] == [
        ("heroic-nile-amazon-id", "Amazon")
    ]
    assert provider.last_report.malformed_records >= 1
    assert provider.last_report.duplicate_roots == 1


def test_heroic_same_title_different_ids_remain_distinct(tmp_path: Path) -> None:
    root = tmp_path / "heroic"
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    _write_json(
        root / "legendaryConfig" / "legendary" / "installed.json",
        {
            "one": {"install_path": str(first), "title": "Same"},
            "two": {"install_path": str(second), "title": "Same"},
        },
    )
    games = HeroicGameProvider(_Filesystem(), roots=(root,)).refresh()
    assert {game.id for game in games} == {"heroic-legendary-one", "heroic-legendary-two"}


def _lutris_root(tmp_path: Path, variant: str = "native") -> LutrisRoot:
    base = tmp_path / variant
    return LutrisRoot(base / "config" / "lutris", base / "data" / "lutris", base / "cache" / "lutris", variant)


def _lutris_db(root: LutrisRoot, rows: list[dict[str, object]]) -> None:
    root.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root.data_dir / "pga.db")
    try:
        connection.execute(
            "CREATE TABLE games (id INTEGER, name TEXT, slug TEXT, platform TEXT, "
            "runner TEXT, executable TEXT, directory TEXT, installed INTEGER, "
            "configpath TEXT, service TEXT, service_id TEXT)"
        )
        for row in rows:
            connection.execute(
                "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(
                    row.get(name)
                    for name in (
                        "id", "name", "slug", "platform", "runner", "executable",
                        "directory", "installed", "configpath", "service", "service_id",
                    )
                ),
            )
        connection.commit()
    finally:
        connection.close()


def test_lutris_database_and_wine_config_correlation(tmp_path: Path) -> None:
    root = _lutris_root(tmp_path)
    install = tmp_path / "Games" / "Wine Game"
    install.mkdir(parents=True)
    _lutris_db(
        root,
        [{
            "id": 42, "name": "Wine Game", "slug": "wine-game", "platform": "Windows",
            "runner": "wine", "directory": str(install), "installed": 1,
            "configpath": "wine-game-1", "service": "gog", "service_id": "123",
        }],
    )
    config = {
        "game": {"exe": "bin/Game.exe", "prefix": str(tmp_path / "prefix")},
        "system": {"working_dir": str(install / "bin")},
    }
    path = root.config_dir / "games" / "wine-game-1.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    cover = root.data_dir / "coverart" / "wine-game.jpg"
    cover.parent.mkdir()
    cover.write_bytes(b"jpeg")
    banner = root.data_dir / "banners" / "wine-game.jpg"
    banner.parent.mkdir()
    banner.write_bytes(b"jpeg")
    icon = (
        root.data_dir.parent
        / "icons"
        / "hicolor"
        / "128x128"
        / "apps"
        / "lutris_wine-game.png"
    )
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"png")

    game = LutrisGameProvider(_Filesystem(), roots=(root,)).refresh()[0]

    assert game.id == "lutris-42"
    assert game.runner == "wine"
    assert game.store == "GOG"
    assert game.executable_path == str(install / "bin" / "Game.exe")
    assert game.wine_prefix == tmp_path / "prefix"
    assert game.working_directory == install / "bin"
    assert game.portrait_artwork_path == cover
    assert game.header_artwork_path == banner
    assert game.fallback_artwork_path == icon
    assert game.launch_uri == "lutris:rungameid/42"


def test_lutris_flatpak_native_linux_and_disconnected_game(tmp_path: Path) -> None:
    root = _lutris_root(tmp_path, "flatpak")
    missing = tmp_path / "removed"
    _lutris_db(
        root,
        [{
            "id": 7, "name": "Native Game", "slug": "native", "platform": "Linux",
            "runner": "linux", "directory": str(missing), "installed": 1,
            "configpath": "native-1",
        }],
    )

    game = LutrisGameProvider(_Filesystem(), roots=(root,)).refresh()[0]
    assert game.launcher_variant == "flatpak"
    assert game.store == "native Linux"
    assert game.status is GameStatus.MISSING_FILES


def test_lutris_malformed_config_and_duplicate_root_are_tolerated(tmp_path: Path) -> None:
    root = _lutris_root(tmp_path)
    install = tmp_path / "game"
    install.mkdir()
    _lutris_db(
        root,
        [{
            "id": 9, "name": "Still Visible", "slug": "visible", "runner": "wine",
            "directory": str(install), "installed": 1, "configpath": "bad",
        }],
    )
    config = root.config_dir / "games" / "bad.yml"
    config.parent.mkdir(parents=True)
    config.write_text("game: [broken", encoding="utf-8")

    provider = LutrisGameProvider(_Filesystem(), roots=(root, root))
    games = provider.refresh()
    assert [game.id for game in games] == ["lutris-9"]
    assert provider.last_report.malformed_records >= 1
    assert provider.last_report.duplicate_roots == 1


def test_lutris_schema_difference_and_malformed_database_are_isolated(tmp_path: Path) -> None:
    malformed = _lutris_root(tmp_path / "bad")
    malformed.data_dir.mkdir(parents=True)
    sqlite3.connect(malformed.data_dir / "pga.db").close()
    valid = _lutris_root(tmp_path / "good")
    install = tmp_path / "installed"
    install.mkdir()
    _lutris_db(
        valid,
        [{"id": 1, "name": "Good", "directory": str(install), "installed": 1, "configpath": "good"}],
    )

    provider = LutrisGameProvider(_Filesystem(), roots=(malformed, valid))
    assert [game.id for game in provider.refresh()] == ["lutris-1"]
    assert any(root.state == "malformed" for root in provider.last_report.roots)


def test_lutris_database_without_installed_column_is_not_guessed(tmp_path: Path) -> None:
    root = _lutris_root(tmp_path)
    root.data_dir.mkdir(parents=True)
    connection = sqlite3.connect(root.data_dir / "pga.db")
    try:
        connection.execute(
            "CREATE TABLE games (id INTEGER, name TEXT, directory TEXT)"
        )
        connection.execute(
            "INSERT INTO games VALUES (?, ?, ?)",
            (11, "Ambiguous", str(tmp_path / "game")),
        )
        connection.commit()
    finally:
        connection.close()

    provider = LutrisGameProvider(_Filesystem(), roots=(root,))
    assert provider.refresh() == ()
    assert provider.last_report.roots[0].state == "malformed"
    assert "installed" in provider.last_report.errors[0]
