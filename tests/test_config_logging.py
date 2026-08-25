from __future__ import annotations

import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib

import pytest
from PySide6.QtGui import QGuiApplication, QImage

from game_optimization_linux import config
from game_optimization_linux.app import (
    _application_icon,
    _extract_runtime_options,
    _set_application_metadata,
)
from game_optimization_linux.desktop_entry import render_desktop_entry
from game_optimization_linux.logging_config import configure_logging, parse_log_level


def test_runtime_metadata_is_centralized() -> None:
    assert config.APP_NAME == "Game Optimization Linux"
    assert config.APP_VERSION
    assert config.MAIN_QML.name == "Main.qml"
    assert config.MAIN_QML.parent == config.QML_DIR
    assert config.APP_ICON.name == "GameOptimizationLinuxIcon.png"
    assert config.APP_ICON.is_file()
    assert config.APP_ICON_SVG.name == "game-optimization-linux-app-icon.svg"
    assert config.APP_ICON_SVG.is_file()
    assert tuple(config.APP_ICON_VARIANTS) == (16, 32, 48, 64, 128, 256, 512)
    desktop_entry = render_desktop_entry()
    assert f"Name={config.APP_NAME}\n" in desktop_entry
    assert f"Icon={config.APP_ID}\n" in desktop_entry


def test_monolithic_core_branding_is_packaged_without_mascot_assets() -> None:
    project_root = Path(__file__).resolve().parents[1]
    qml_root = config.QML_DIR
    runtime_sources = [
        *qml_root.rglob("*.qml"),
        project_root / "pyproject.toml",
        project_root / "flatpak" / f"{config.APP_ID}.yml",
    ]
    for path in runtime_sources:
        source = path.read_text(encoding="utf-8")
        assert "App.Branding" not in source, path
        assert "pingwin-" not in source, path
        assert "pingwin" not in source.lower(), path
    assert not (qml_root / "Branding.qml").exists()
    branding_dir = config.PACKAGE_DIR / "assets" / "branding"
    expected = {
        "game-optimization-linux-app-icon.svg",
        "game-optimization-linux-horizontal-dark.svg",
        "game-optimization-linux-horizontal.svg",
        "game-optimization-linux-monochrome.svg",
        "game-optimization-linux-symbol.svg",
        *{
            f"game-optimization-linux-{size}x{size}.png"
            for size in (16, 32, 48, 64, 128, 256, 512)
        },
    }
    assert {path.name for path in branding_dir.iterdir()} == expected
    assert config.APP_ICON.read_bytes() == (
        branding_dir / "game-optimization-linux-512x512.png"
    ).read_bytes()
    scalable = (
        project_root
        / "data/icons/hicolor/scalable/apps"
        / f"{config.APP_ID}.svg"
    )
    assert scalable.read_bytes() == config.APP_ICON_SVG.read_bytes()
    package_data = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["setuptools"]["package-data"]["game_optimization_linux"]
    assert "assets/branding/*.svg" in package_data
    assert "assets/branding/*.png" in package_data


def test_desktop_entry_and_opt_in_installer_use_the_same_app_id() -> None:
    project_root = Path(__file__).resolve().parents[1]
    desktop_path = project_root / "data" / f"{config.APP_ID}.desktop"
    installer = project_root / "scripts" / "install-desktop-entry.sh"

    assert desktop_path.read_text(encoding="utf-8") == render_desktop_entry()
    installer_source = installer.read_text(encoding="utf-8")
    assert installer.stat().st_mode & 0o111
    assert f'app_id="{config.APP_ID}"' in installer_source
    assert "$data_home/applications/$app_id.desktop" in installer_source
    assert "$data_home/icons/hicolor/${size}x${size}/apps/$app_id.png" in installer_source
    assert "$data_home/icons/hicolor/scalable/apps/$app_id.svg" in installer_source
    assert "$data_home/metainfo/$app_id.metainfo.xml" in installer_source
    assert "sudo" not in installer_source
    assert "--dev" in installer_source
    assert f"StartupWMClass={config.APP_ID}\n" in desktop_path.read_text(
        encoding="utf-8"
    )
    desktop_exec = next(
        line.removeprefix("Exec=")
        for line in desktop_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Exec=")
    )
    assert shlex.split(desktop_exec) == ["game-optimization-linux"]
    assert "/app/bin/flatpak" not in desktop_exec

    manifest = (
        project_root / "flatpak" / f"{config.APP_ID}.yml"
    ).read_text(encoding="utf-8")
    assert "command: game-optimization-linux\n" in manifest
    assert (
        f"share/icons/hicolor/scalable/apps/{config.APP_ID}.svg" in manifest
    )
    project = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["scripts"]["game-optimization-linux"] == (
        "game_optimization_linux.main:main"
    )

    build_instructions = (
        project_root / "flatpak" / "README.md"
    ).read_text(encoding="utf-8")
    builder_command = build_instructions.split("```bash", 1)[1].split("```", 1)[0]
    assert "--install" not in builder_command
    assert "--repo=.flatpak-build-repo" in builder_command
    _set_application_metadata()
    assert QGuiApplication.desktopFileName() == config.APP_ID
    assert QGuiApplication.applicationName() == config.APP_NAME
    assert QGuiApplication.organizationName() == config.ORGANIZATION_NAME
    validator = shutil.which("desktop-file-validate")
    if validator is not None:
        completed = subprocess.run(
            [validator, str(desktop_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(
    os.environ.get("GAME_OPTIMIZATION_INSTALLED_FLATPAK_TEST") != "1",
    reason="requires the freshly installed Flatpak",
)
def test_installed_flatpak_export_and_main_command_start() -> None:
    exported = (
        Path.home()
        / ".local/share/flatpak/exports/share/applications"
        / f"{config.APP_ID}.desktop"
    )
    content = exported.read_text(encoding="utf-8")
    command = next(
        line.removeprefix("Exec=")
        for line in content.splitlines()
        if line.startswith("Exec=")
    )
    argv = shlex.split(command)
    assert argv[0] == shutil.which("flatpak")
    assert argv[0] != "/app/bin/flatpak"
    assert "--command=game-optimization-linux" in argv
    assert config.APP_ID in argv

    completed = subprocess.run(
        [
            "timeout", "--signal=TERM", "6s",
            argv[0], "run", "--user",
            "--env=GAME_OPTIMIZATION_DEMO=1",
            "--env=QT_QPA_PLATFORM=offscreen",
            config.APP_ID, "--desktop", "-platform", "offscreen",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 124, completed.stdout
    assert "Starting Game Optimization Linux" in completed.stdout
    assert "Stopped Game Optimization controller" in completed.stdout


def test_desktop_installer_uses_a_working_launcher_in_temporary_xdg_home(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    installer = project_root / "scripts" / "install-desktop-entry.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launcher = fake_bin / "game-optimization-linux"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    data_home = tmp_path / "share"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    environment["XDG_DATA_HOME"] = str(data_home)
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    completed = subprocess.run(
        [str(installer)],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    desktop = data_home / "applications" / f"{config.APP_ID}.desktop"
    icon = data_home / "icons" / "hicolor" / "256x256" / "apps" / f"{config.APP_ID}.png"
    scalable_icon = (
        data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{config.APP_ID}.svg"
    )
    assert desktop.is_file()
    assert icon.is_file()
    assert scalable_icon.read_bytes() == config.APP_ICON_SVG.read_bytes()
    assert f'Exec="{launcher}"' in desktop.read_text(encoding="utf-8")
    diagnostic = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "diagnose-desktop-integration.py"),
        ],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert diagnostic.returncode == 0, diagnostic.stdout + diagnostic.stderr
    assert f"desktop entry: {desktop}" in diagnostic.stdout
    assert "icon 256x256:" in diagnostic.stdout
    assert "alpha_bbox=" in diagnostic.stdout


def test_desktop_installer_dev_mode_prefers_project_virtual_environment(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[1]
    project = tmp_path / "checkout"
    (project / "scripts").mkdir(parents=True)
    (project / "data").mkdir()
    (project / "src" / "game_optimization_linux" / "resources").mkdir(parents=True)
    (project / "src" / "game_optimization_linux" / "assets").mkdir(parents=True)
    (project / ".venv" / "bin").mkdir(parents=True)
    shutil.copy2(source_root / "scripts" / "install-desktop-entry.sh", project / "scripts")
    shutil.copy2(
        source_root / "data" / f"{config.APP_ID}.desktop",
        project / "data",
    )
    shutil.copy2(
        source_root / "data" / f"{config.APP_ID}.metainfo.xml",
        project / "data",
    )
    shutil.copytree(source_root / "data" / "icons", project / "data" / "icons")
    shutil.copytree(
        source_root / "src" / "game_optimization_linux" / "assets" / "branding",
        project / "src" / "game_optimization_linux" / "assets" / "branding",
    )
    launcher = project / ".venv" / "bin" / "game-optimization-linux"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    data_home = tmp_path / "dev-share"
    environment = os.environ.copy()
    environment["PATH"] = "/usr/bin:/bin"
    environment["XDG_DATA_HOME"] = str(data_home)
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    completed = subprocess.run(
        [str(project / "scripts" / "install-desktop-entry.sh"), "--dev"],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    installed = data_home / "applications" / f"{config.APP_ID}.desktop"
    assert f'Exec="{launcher}"' in installed.read_text(encoding="utf-8")


def test_hicolor_icons_are_square_native_variants_and_appstream_id_matches() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for size in (16, 32, 48, 64, 128, 256, 512):
        path = (
            project_root
            / "data"
            / "icons"
            / "hicolor"
            / f"{size}x{size}"
            / "apps"
            / f"{config.APP_ID}.png"
        )
        image = QImage(str(path))
        assert not image.isNull()
        assert (image.width(), image.height()) == (size, size)
        supplied = (
            config.BRANDING_DIR / f"game-optimization-linux-{size}x{size}.png"
        )
        assert path.read_bytes() == supplied.read_bytes()
        opaque = [
            (x, y)
            for y in range(size)
            for x in range(size)
            if image.pixelColor(x, y).alpha() > 0
        ]
        assert opaque
        left = min(x for x, _ in opaque)
        right = max(x for x, _ in opaque)
        top = min(y for _, y in opaque)
        bottom = max(y for _, y in opaque)
        assert (right - left + 1) / size >= 0.90
        assert (bottom - top + 1) / size >= 0.90
    metainfo = (
        project_root / "data" / f"{config.APP_ID}.metainfo.xml"
    ).read_text(encoding="utf-8")
    assert f"<id>{config.APP_ID}</id>" in metainfo
    assert f">{config.APP_ID}.desktop</launchable>" in metainfo


def test_monolithic_core_variants_and_multi_size_qicon_are_diagnostic_ready() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QGuiApplication.instance() or QGuiApplication(
        ["game-optimization-icon-test"]
    )
    branding_dir = config.BRANDING_DIR
    assert not QImage(str(config.APP_ICON_SVG)).isNull()
    for size in config.APP_ICON_VARIANTS:
        path = config.APP_ICON_VARIANTS[size]
        image = QImage(str(path))
        assert not image.isNull()
        supplied = branding_dir / f"game-optimization-linux-{size}x{size}.png"
        assert path.read_bytes() == supplied.read_bytes()

    icon = _application_icon()
    assert not icon.isNull()
    available = {(size.width(), size.height()) for size in icon.availableSizes()}
    assert {(size, size) for size in config.APP_ICON_VARIANTS} <= available
    assert application is QGuiApplication.instance()


def test_emergency_ui_arguments_are_removed_before_qt_parsing() -> None:
    qt_args, desktop, reset = _extract_runtime_options(
        ["game-optimization-linux", "--desktop", "-platform", "offscreen"]
    )
    assert qt_args == ["game-optimization-linux", "-platform", "offscreen"]
    assert desktop is True
    assert reset is False

    qt_args, desktop, reset = _extract_runtime_options(
        ["game-optimization-linux", "--reset-ui-mode"]
    )
    assert qt_args == ["game-optimization-linux"]
    assert desktop is False
    assert reset is True


def test_parse_log_level_accepts_names_and_falls_back() -> None:
    assert parse_log_level("debug") == logging.DEBUG
    assert parse_log_level(logging.WARNING) == logging.WARNING
    assert parse_log_level("not-a-level") == logging.INFO


def test_logging_setup_is_idempotent_and_writes_to_requested_file(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "logs" / "game-optimization-linux.log"

    assert configure_logging("DEBUG", log_file=log_file) == log_file
    assert configure_logging("INFO", log_file=log_file) == log_file

    logging.getLogger("game_optimization_linux.test").warning("demo warning")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "demo warning" in log_file.read_text(encoding="utf-8")
