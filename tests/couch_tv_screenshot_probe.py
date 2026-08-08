#!/usr/bin/env python3
"""Render deterministic Couch Mode screenshots without inspecting real games.

The probe owns all of its inputs: an in-memory demo provider, synthetic task
rows, a fake controller, and generated PNG artwork.  It never starts a Steam
scan, compression, a launcher, SDL, Polkit, or a privileged measurement.

By default the screenshots are kept in a newly-created temporary directory so
they can be inspected after the process exits.  Pass ``--output`` to choose a
different destination.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = Path(
    os.environ.get("GAMEFORGE_SRC_ROOT", str(ROOT / "src"))
)
QML_ROOT = Path(
    os.environ.get("GAMEFORGE_QML_ROOT", str(SRC_ROOT / "gameforge" / "qml"))
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create safe synthetic Couch Mode reference screenshots."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: a persistent directory under /tmp).",
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use the production local-Steam controller instead of synthetic fixtures.",
    )
    parser.add_argument("--wait-seconds", type=float, default=45.0)
    return parser.parse_args()


def _prepare_environment(output: Path, *, live: bool) -> None:
    xdg_root = output / "_xdg"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    os.environ["XDG_CONFIG_HOME"] = str(xdg_root / "config")
    os.environ["XDG_CACHE_HOME"] = str(xdg_root / "cache")
    os.environ["XDG_STATE_HOME"] = str(xdg_root / "state")
    if live:
        os.environ.pop("GAMEFORGE_DEMO", None)
    else:
        os.environ["GAMEFORGE_DEMO"] = "1"


def _settle(application: Any, rounds: int = 48) -> None:
    for _ in range(rounds):
        application.processEvents()
        time.sleep(0.004)


def _create_artwork(output: Path, application: Any) -> list[tuple[Path, Path]]:
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import (
        QBrush,
        QColor,
        QFont,
        QImage,
        QLinearGradient,
        QPainter,
        QPen,
    )

    artwork_root = output / "_synthetic_artwork"
    artwork_root.mkdir(parents=True, exist_ok=True)
    palette = (
        ("#164E63", "#38BDF8"),
        ("#4C1D95", "#C084FC"),
        ("#713F12", "#FBBF24"),
        ("#14532D", "#4ADE80"),
        ("#7F1D1D", "#FB7185"),
        ("#1E3A8A", "#60A5FA"),
        ("#134E4A", "#2DD4BF"),
        ("#581C87", "#E879F9"),
    )
    names = (
        "Astral Forge",
        "Verdant Signal",
        "Neon Bastion",
        "Silent Meridian",
        "Ember Circuit",
        "Polar Drift",
        "Echoes of Alloy",
        "Luminous Vale",
    )

    def render(path: Path, width: int, height: int, index: int, title: str) -> None:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#101722"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0.0, QColor(palette[index][0]))
        gradient.setColorAt(0.58, QColor(palette[index][1]))
        gradient.setColorAt(1.0, QColor("#090D14"))
        painter.fillRect(image.rect(), QBrush(gradient))

        painter.setPen(QPen(QColor(255, 255, 255, 44), max(3, width // 150)))
        for ring in range(1, 5):
            diameter = int(min(width, height) * (0.15 + ring * 0.14))
            center_x = int(width * (0.68 if width > height else 0.5))
            center_y = int(height * 0.38)
            painter.drawEllipse(
                QRect(center_x - diameter // 2, center_y - diameter // 2,
                      diameter, diameter)
            )

        accent = QColor(palette[index][1])
        accent.setAlpha(205)
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        symbol_size = int(min(width, height) * 0.19)
        painter.drawRoundedRect(
            QRect(int(width * 0.12), int(height * 0.14), symbol_size, symbol_size),
            symbol_size * 0.22,
            symbol_size * 0.22,
        )

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont(application.font().family(), max(18, width // 18), QFont.Weight.Bold))
        title_rect = QRect(
            int(width * 0.10), int(height * 0.66), int(width * 0.80), int(height * 0.20)
        )
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextWordWrap),
            title,
        )
        painter.setPen(QColor(255, 255, 255, 190))
        painter.setFont(QFont(application.font().family(), max(12, width // 35), QFont.Weight.DemiBold))
        painter.drawText(
            QRect(int(width * 0.10), int(height * 0.88), int(width * 0.80), int(height * 0.07)),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "GAMEFORGE  ·  SYNTHETIC",
        )
        painter.end()
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"Could not save synthetic artwork: {path}")

    paths: list[tuple[Path, Path]] = []
    for index, title in enumerate(names):
        game_root = artwork_root / f"game-{index + 1:02d}"
        game_root.mkdir(parents=True, exist_ok=True)
        portrait = game_root / "portrait.png"
        header = game_root / "header.png"
        render(portrait, 600, 900, index, title)
        render(header, 1920, 640, index, title)
        paths.append((portrait, header))
    return paths


def _synthetic_games(output: Path, artwork: Sequence[tuple[Path, Path]]) -> tuple[Any, ...]:
    from gameforge.models import FilesystemType, Game, GameStatus, Launcher

    names = (
        "Astral Forge",
        "Verdant Signal",
        "Neon Bastion",
        "Silent Meridian",
        "Ember Circuit",
        "Polar Drift",
        "Echoes of Alloy",
        "Luminous Vale",
    )
    results = []
    synthetic_library = output / "_synthetic_library"
    for index, (name, paths) in enumerate(zip(names, artwork, strict=True), start=1):
        portrait, header = paths
        filesystem = FilesystemType.BTRFS if index != 6 else FilesystemType.EXT4
        results.append(
            Game(
                id=f"tv-demo-{index:02d}",
                name=name,
                launcher=Launcher.STEAM,
                install_path=synthetic_library / name,
                logical_size_gb=18.0 + index * 7.35,
                physical_size_gb=15.0 + index * 6.8,
                filesystem=filesystem,
                filesystem_name=filesystem.value,
                compression_available=filesystem is FilesystemType.BTRFS,
                saved_space_gb=0.8 + index * 0.24,
                status=GameStatus.READY,
                portrait_artwork_path=portrait,
                header_artwork_path=header,
                fallback_artwork_path=header,
                steam_app_id=str(900000 + index),
                library_path=synthetic_library,
                data_source="Synthetic Couch screenshot fixture",
                library_available=True,
                is_writable=True,
            )
        )
    return tuple(results)


class _SyntheticTaskService:
    """Fixed in-memory task rows; no worker, subprocess, or filesystem work."""

    def __init__(self, tasks: Sequence[Any]) -> None:
        self._tasks = list(tasks)

    def list_tasks(self) -> tuple[Any, ...]:
        return tuple(self._tasks)

    def tick(self, step: float = 1.0) -> tuple[Any, ...]:
        del step
        return self.list_tasks()

    def cancel(self, task_id: str) -> Any:
        from gameforge.models import TaskStatus

        for index, task in enumerate(self._tasks):
            if task.id == task_id:
                self._tasks[index] = replace(task, status=TaskStatus.CANCELLED)
                return self._tasks[index]
        raise KeyError(task_id)

    def shutdown(self, **_kwargs: Any) -> None:
        return None


def _synthetic_tasks() -> tuple[Any, ...]:
    from gameforge.models import Task, TaskStatus, TaskType

    return (
        Task(
            id="tv-task-active",
            game_id="tv-demo-01",
            game_name="Astral Forge",
            task_type=TaskType.ANALYSIS,
            title="Analyze Astral Forge",
            status=TaskStatus.RUNNING,
            progress=63.0,
            metadata={
                "stage": "Sampling files",
                "current_file": "Synthetic/Data/Textures.pack",
                "scanned_files": 1248,
                "analyzed_bytes": 3_221_225_472,
                "cancellable": True,
                "read_only": True,
            },
        ),
        Task(
            id="tv-task-queued",
            game_id="tv-demo-02",
            game_name="Verdant Signal",
            task_type=TaskType.VERIFICATION,
            title="Verify Verdant Signal",
            status=TaskStatus.QUEUED,
            progress=0.0,
            metadata={"stage": "Waiting", "cancellable": True, "read_only": True},
        ),
        Task(
            id="tv-task-complete",
            game_id="tv-demo-03",
            game_name="Neon Bastion",
            task_type=TaskType.OPTIMIZATION,
            title="Optimization preview",
            status=TaskStatus.COMPLETED,
            progress=100.0,
            metadata={"stage": "Preview ready", "cancellable": False, "read_only": True},
        ),
        Task(
            id="tv-task-failed",
            game_id="tv-demo-04",
            game_name="Silent Meridian",
            task_type=TaskType.ANALYSIS,
            title="Analysis preview",
            status=TaskStatus.FAILED,
            progress=41.0,
            error="Synthetic permission warning",
            metadata={"stage": "Stopped", "cancellable": False, "read_only": True},
        ),
    )


def _controller(application: Any, output: Path, games: Sequence[Any]) -> Any:
    from gameforge.controllers import AppController
    from gameforge.models import GamepadDevice, GamepadType
    from gameforge.providers import DemoGameProvider, FakeGamepadProvider
    from gameforge.services import GamepadService, SettingsStore

    fake_controller = GamepadDevice(
        instance_id=1,
        name="Synthetic TV Controller",
        gamepad_type=GamepadType.XBOX,
        mapping_status="Synthetic mapping",
        battery_percent=86,
    )
    service = GamepadService(FakeGamepadProvider((fake_controller,), available=True))
    controller = AppController(
        parent=application,
        game_provider=DemoGameProvider(games),
        task_service=_SyntheticTaskService(_synthetic_tasks()),
        settings_store=SettingsStore(output / "_xdg" / "settings.json"),
        gamepad_service=service,
        initial_games=games,
        demo_mode=True,
        auto_refresh=False,
        initial_interface_mode="couch",
    )
    # Keep screenshot data frozen.  The rows are already loaded synchronously.
    controller._task_timer.stop()
    # Select the synthetic device without emitting any button action into QML.
    # This keeps the screenshots deterministic while exercising Xbox hints.
    controller._gamepad_service._set_active(fake_controller.instance_id)
    if controller.updates:
        raise RuntimeError("The synthetic Updates fixture must remain empty")
    return controller


def _live_controller(application: Any, output: Path) -> Any:
    """Create the production read-only Steam controller in isolated XDG state."""

    from gameforge.controllers import AppController
    from gameforge.services import SettingsStore

    return AppController(
        parent=application,
        settings_store=SettingsStore(output / "_xdg" / "settings.json"),
        initial_interface_mode="couch",
        demo_mode=False,
        auto_refresh=True,
    )


def _wait_for_live_library(application: Any, controller: Any, timeout: float) -> None:
    deadline = time.monotonic() + max(1.0, timeout)
    observed_scan = bool(controller.isScanning)
    while time.monotonic() < deadline:
        application.processEvents()
        observed_scan = observed_scan or bool(controller.isScanning)
        if controller.games and (observed_scan and not controller.isScanning):
            return
        time.sleep(0.025)
    if not controller.games:
        raise RuntimeError(
            "The production Steam scan completed without a visible game; "
            f"status={controller.libraryScanStatus!r}, "
            f"message={controller.libraryScanMessage!r}"
        )


def _invoke(root: Any, method: str, *values: str) -> None:
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    arguments = [Q_ARG("QVariant", value) for value in values]
    if not QMetaObject.invokeMethod(root, method, Qt.ConnectionType.DirectConnection, *arguments):
        raise RuntimeError(f"Could not invoke QML method {method}")


def _capture(view: Any, application: Any, path: Path, width: int, height: int) -> None:
    _settle(application, 70)
    image = view.grabWindow()
    if image.isNull():
        raise RuntimeError(f"Qt returned an empty screenshot for {path.name}")
    if image.width() != width or image.height() != height:
        raise RuntimeError(
            f"Unexpected screenshot dimensions for {path.name}: "
            f"{image.width()}x{image.height()}, expected {width}x{height}"
        )
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save screenshot: {path}")


def _render(
    application: Any,
    output: Path,
    controller: Any,
    width: int,
    height: int,
    theme_name: str,
    selected_game_id: str,
) -> list[Path]:
    from PySide6.QtCore import QUrl
    from PySide6.QtQuick import QQuickItem, QQuickView

    # CouchMain normally inherits this binding from Main.qml.  The probe loads
    # it directly, so a tiny generated wrapper supplies the same theme binding.
    wrapper = output / "_CouchScreenshotRoot.qml"
    couch_import = QUrl.fromLocalFile(str(QML_ROOT / "couch")).toString()
    app_import = QUrl.fromLocalFile(str(QML_ROOT)).toString()
    wrapper.write_text(
        "\n".join(
            (
                "import QtQuick",
                f'import "{app_import}" as App',
                f'import "{couch_import}" as Couch',
                "Couch.CouchMain {",
                "    id: screen",
                f'    property string probeTheme: "{theme_name}"',
                '    Binding { target: App.Theme; property: "mode"; value: screen.probeTheme }',
                "}",
            )
        ),
        encoding="utf-8",
    )
    view = QQuickView()
    view.engine().addImportPath(str(QML_ROOT))
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(width, height)
    view.setSource(QUrl.fromLocalFile(str(wrapper)))
    if view.status() == QQuickView.Status.Error:
        errors = "; ".join(error.toString() for error in view.errors())
        raise RuntimeError(f"CouchMain.qml failed to load: {errors}")
    root = view.rootObject()
    if not isinstance(root, QQuickItem):
        raise RuntimeError("CouchMain.qml did not create a QQuickItem root")

    root.setProperty("controller", controller)
    view.show()
    root.forceActiveFocus()
    _settle(application, 80)

    def show_library_filter() -> None:
        _invoke(root, "setSection", "library", selected_game_id)
        _invoke(root, "handleAction", "ContextMenu")

    def show_game_context() -> None:
        _invoke(root, "setSection", "home", selected_game_id)
        _invoke(root, "handleAction", "ContextMenu")

    def show_details() -> None:
        _invoke(root, "handleAction", "Back")  # close the filter only
        _invoke(root, "openGameFrom", "library", selected_game_id)

    def show_system_menu() -> None:
        _invoke(root, "setSection", "home", selected_game_id)
        _invoke(root, "handleAction", "Back")

    def show_quit_confirmation() -> None:
        if not bool(root.property("pageModalOpen")):
            for _ in range(5):
                _invoke(root, "handleAction", "NavigateDown")
            _invoke(root, "handleAction", "Confirm")

    captures: list[tuple[str, Any]] = [
        ("home.png", lambda: _invoke(root, "setSection", "home", selected_game_id)),
        ("game-context.png", show_game_context),
        ("library.png", lambda: _invoke(root, "setSection", "library", selected_game_id)),
        ("library-filter.png", show_library_filter),
        ("details.png", show_details),
        ("updates.png", lambda: controller.navigate("updates")),
        ("tasks.png", lambda: controller.navigate("tasks")),
        ("settings.png", lambda: controller.navigate("settings")),
        ("system-menu.png", show_system_menu),
        ("quit-confirmation.png", show_quit_confirmation),
    ]
    paths: list[Path] = []
    try:
        for filename, select_page in captures:
            select_page()
            _settle(application, 32)
            path = output / filename
            _capture(view, application, path, width, height)
            paths.append(path)
    finally:
        view.close()
        _settle(application, 8)
    return paths


def main() -> int:
    args = _arguments()
    if args.width < 1280 or args.height < 720:
        raise SystemExit("Couch screenshot resolution must be at least 1280x720")
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else Path(tempfile.mkdtemp(prefix="gameforge-couch-tv-screenshots-"))
    )
    output.mkdir(parents=True, exist_ok=True)
    _prepare_environment(output, live=args.live)

    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication

    messages: list[str] = []

    def message_handler(_mode: object, _context: object, message: str) -> None:
        messages.append(str(message))

    qInstallMessageHandler(message_handler)
    application = QGuiApplication([sys.argv[0]])
    controller = None
    try:
        if args.live:
            controller = _live_controller(application, output)
            _wait_for_live_library(application, controller, args.wait_seconds)
            # The offscreen platform cannot receive the host SDL device. Hide
            # only the reconnect overlay in this audit harness so the real
            # production pages underneath can be inspected. Production
            # GamepadService and hotplug state are not changed.
            controller.couchNavigation.setControllerConnected(True)
            selected_game_id = str(controller.games[0]["id"])
        else:
            artwork = _create_artwork(output, application)
            games = _synthetic_games(output, artwork)
            controller = _controller(application, output, games)
            selected_game_id = "tv-demo-01"
        screenshots = _render(
            application,
            output,
            controller,
            args.width,
            args.height,
            args.theme,
            selected_game_id,
        )
    finally:
        if controller is not None:
            controller.shutdown()
        _settle(application, 5)

    critical = [
        message
        for message in messages
        if any(
            marker in message
            for marker in (
                "Unable to assign",
                "Binding loop",
                "Layout polish loop",
                "Cannot create delegate",
                "ReferenceError",
                "TypeError",
            )
        )
    ]
    if critical:
        raise RuntimeError("Critical QML warning: " + " | ".join(critical))

    manifest = {
        "mode": "production-local-steam" if args.live else "synthetic-demo-only",
        "resolution": [args.width, args.height],
        "theme": args.theme,
        "realScansStarted": bool(args.live),
        "realGamesAccessed": bool(args.live),
        "screenshots": [str(path) for path in screenshots],
        "artworkDirectory": (
            None if args.live else str(output / "_synthetic_artwork")
        ),
        "controllerOverlaySuppressedForOffscreenCapture": bool(args.live),
        "qmlMessageCount": len(messages),
        "artworkLifecycleMessageCount": sum(
            "GameArtwork lifecycle" in message for message in messages
        ),
        "criticalQmlWarnings": [],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({**manifest, "manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
