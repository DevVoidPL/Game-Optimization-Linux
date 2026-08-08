"""End-to-end OptiScaler smoke probe intended for an installed Flatpak.

This is deliberately a standalone script rather than a pytest mock: it creates
and reads a real 7z archive through the installed py7zr package, instantiates
the installed QML file picker, and mutates only a temporary synthetic game.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time

import py7zr
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

import game_optimization_linux
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services import (
    GameExecutableResolver,
    OptiScalerReleaseClient,
    OptiScalerProfileRepository,
    OptiScalerService,
    open_archive,
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _picker_filters(application: QGuiApplication) -> list[str]:
    qml_root = Path(game_optimization_linux.__file__).resolve().parent / "qml"
    view = QQuickView()
    view.engine().addImportPath(str(qml_root))
    view.setSource(
        QUrl.fromLocalFile(
            str(qml_root / "pages" / "details" / "OptiScalerSection.qml")
        )
    )
    for _ in range(20):
        application.processEvents()
        time.sleep(0.005)
    if view.status() != QQuickView.Status.Ready:
        raise RuntimeError("; ".join(error.toString() for error in view.errors()))
    root = view.rootObject()
    if root is None:
        raise RuntimeError("the installed OptiScaler QML did not create a root item")
    value = root.property("archiveNameFilters")
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    filters = [str(item) for item in (value or [])]
    view.close()
    return filters


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QGuiApplication.instance() or QGuiApplication([])
    with tempfile.TemporaryDirectory(prefix="game-optimization-flatpak-optiscaler-") as raw:
        root = Path(raw)
        game_root = root / "Synthetic Game"
        install_directory = game_root / "Binaries" / "Win64"
        install_directory.mkdir(parents=True)
        executable = install_directory / "Synthetic-Win64-Shipping.exe"
        executable.write_bytes(b"synthetic executable")
        original_proxy = install_directory / "dxgi.dll"
        original_proxy.write_bytes(b"original user proxy")
        original_hash = _digest(original_proxy)

        online = os.environ.get("GAME_OPTIMIZATION_TEST_OFFICIAL_ONLINE") == "1"
        official_archive = os.environ.get(
            "GAME_OPTIMIZATION_TEST_OFFICIAL_ARCHIVE", ""
        ).strip()
        official_fixture = bool(official_archive)
        release_version = ""
        archive_sha256 = ""
        second_from_cache = False
        if online:
            client = OptiScalerReleaseClient(root / "online-cache", timeout=45)
            release = client.latest_release(
                force_refresh=True,
                allow_stale_cache=False,
            )
            cached_archive = client.ensure_archive(release)
            archive_path = cached_archive.path
            archive_sha256 = cached_archive.sha256
            release_version = release.version
            second_from_cache = client.ensure_archive(release).from_cache
        elif official_fixture:
            archive_path = Path(official_archive).resolve(strict=True)
            archive_sha256 = _digest(archive_path)
            release_version = os.environ.get(
                "GAME_OPTIMIZATION_TEST_OFFICIAL_VERSION", "official"
            )
        else:
            archive_path = root / "OptiScaler_v0.7.7.7z"
            with py7zr.SevenZipFile(archive_path, "w") as archive:
                archive.writestr(b"optiscaler proxy", "release/OptiScaler.dll")
                archive.writestr(b"[OptiScaler]\n", "release/OptiScaler.ini")
                archive.writestr(b"plugin", "release/plugins/plugin.dll")

        reader = open_archive(archive_path)
        archive_entries = sorted(
            entry.relative_path for entry in reader.entries if not entry.is_directory
        )
        validation_root = root / "validated"
        validation_root.mkdir()
        reader.extract_to(validation_root)

        game = Game(
            id="steam-999999",
            steam_app_id="999999",
            name="Synthetic Game",
            launcher=Launcher.STEAM,
            install_path=game_root,
            logical_size_gb=0.01,
            physical_size_gb=0.01,
            filesystem=FilesystemType.EXT4,
            compression_available=False,
        )
        service = OptiScalerService(
            profile_repository=OptiScalerProfileRepository(root / "config" / "games"),
            data_root=root / "data" / "games",
            executable_resolver=GameExecutableResolver(),
            process_detector=lambda _path: (),
        )
        plan = service.plan(game, archive_path)
        installed = service.install(
            game,
            archive_path,
            allow_replace_conflicts=True,
            expected_archive_sha256=archive_sha256,
        )
        installed_proxy_hash = _digest(original_proxy)
        manifest_path = service.manifest_path(
            installed.app_id, installed.manifest_id
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        removed = service.remove(game)
        restored = service.restore(game)
        restored_hash = _digest(original_proxy)

        result = {
            "flatpak_id": os.environ.get("FLATPAK_ID", ""),
            "official_online": online,
            "official_archive": official_fixture,
            "release_version": release_version,
            "archive_sha256": archive_sha256,
            "second_from_cache": second_from_cache,
            "py7zr_version": py7zr.__version__,
            "picker_filters": _picker_filters(application),
            "archive_format": reader.format_name,
            "archive_entries": archive_entries,
            "validated_extraction": any(
                path.is_file()
                for path in (
                    validation_root / "OptiScaler.dll",
                    validation_root / "release" / "OptiScaler.dll",
                )
            ),
            "plan_can_install": plan.can_install,
            "plan_proxy": plan.injection_dll,
            "plan_format": plan.archive_format,
            "conflict_detected": any(
                item.relative_path == "dxgi.dll" for item in plan.conflicts
            ),
            "installed_proxy_changed": installed_proxy_hash != original_hash,
            "manifest_archive_format": manifest.get("archive_format"),
            "remove_state": removed.installation_state,
            "restore_state": restored.installation_state,
            "restored_hash_matches": restored_hash == original_hash,
            "created_files_removed": not (
                install_directory / "OptiScaler.ini"
            ).exists(),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        expected_filter = "OptiScaler archives (*.7z *.zip)"
        success = all(
            (
                result["flatpak_id"] == "io.github.DevVoidPL.GameOptimizationLinux",
                result["py7zr_version"] == "1.1.3",
                expected_filter in result["picker_filters"],
                result["archive_format"] == "7Z",
                result["validated_extraction"],
                result["plan_can_install"],
                result["plan_format"] == "7Z",
                result["conflict_detected"],
                result["installed_proxy_changed"],
                result["manifest_archive_format"] == "7Z",
                result["remove_state"] == "restore_required",
                result["restore_state"] == "removed",
                result["restored_hash_matches"],
                result["created_files_removed"],
                not online or bool(result["release_version"]),
                not online or len(str(result["archive_sha256"])) == 64,
                not online or result["second_from_cache"],
                not official_fixture or bool(result["release_version"]),
                not official_fixture or len(str(result["archive_sha256"])) == 64,
            )
        )
        return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
