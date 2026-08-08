"""Qt application bootstrap for Game Optimization Linux."""

from __future__ import annotations

from collections.abc import Sequence
import logging
import os
import signal
import sys
from types import FrameType
from typing import Any

from PySide6.QtCore import QMetaObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine, QQmlError

from .config import (
    APP_ID,
    APP_ICON,
    APP_ICON_VARIANTS,
    APP_NAME,
    APP_VERSION,
    MAIN_QML,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
    QML_DIR,
)
from .controllers import AppController
from .logging_config import configure_logging
from .translations import TranslationManager
from .services.host_bootstrap import (
    HostBootstrapError,
    bootstrap_flatpak_host_components,
)


logger = logging.getLogger(__name__)
ROOT_STARTUP_ERROR = (
    f"{APP_NAME} refuses to start with root privileges. "
    "Run it as a regular desktop user."
)


def _extract_runtime_options(
    arguments: Sequence[str],
) -> tuple[list[str], bool, bool]:
    """Remove Game Optimization-only flags before Qt parses its own arguments."""

    qt_arguments: list[str] = []
    force_desktop = False
    reset_ui_mode = False
    for argument in arguments:
        if argument == "--desktop":
            force_desktop = True
        elif argument == "--reset-ui-mode":
            reset_ui_mode = True
        else:
            qt_arguments.append(str(argument))
    if not qt_arguments:
        qt_arguments.append("game-optimization-linux")
    return qt_arguments, force_desktop, reset_ui_mode


def is_running_as_root() -> bool:
    """Return whether the current process has an effective root identity."""

    get_effective_user_id = getattr(os, "geteuid", None)
    return bool(get_effective_user_id is not None and get_effective_user_id() == 0)


def _log_qml_warnings(warnings: list[QQmlError]) -> None:
    for warning in warnings:
        logger.error("QML: %s", warning.toString())


def _set_application_metadata() -> None:
    QGuiApplication.setApplicationName(APP_NAME)
    QGuiApplication.setApplicationDisplayName(APP_NAME)
    QGuiApplication.setApplicationVersion(APP_VERSION)
    QGuiApplication.setOrganizationName(ORGANIZATION_NAME)
    QGuiApplication.setOrganizationDomain(ORGANIZATION_DOMAIN)
    QGuiApplication.setDesktopFileName(APP_ID)


def _prepare_qml_shutdown(engine: QQmlApplicationEngine) -> None:
    """Stop page incubation before the QML engine starts destroying objects."""

    for root in engine.rootObjects():
        try:
            QMetaObject.invokeMethod(
                root,
                "prepareForShutdown",
                Qt.ConnectionType.DirectConnection,
            )
        except RuntimeError:
            # The native window may already have been deleted by the platform.
            logger.debug("QML root was already deleted during shutdown")
    engine.collectGarbage()


def _application_icon() -> QIcon:
    """Build the window icon with native assets for every advertised size."""

    icon = QIcon()
    for size, path in sorted(APP_ICON_VARIANTS.items()):
        if path.is_file():
            icon.addFile(str(path), QSize(size, size), QIcon.Normal, QIcon.Off)
    if icon.isNull() and APP_ICON.is_file():
        icon.addFile(str(APP_ICON))
    return icon


def _install_termination_handlers(
    application: QGuiApplication,
) -> tuple[list[int | None], dict[int, Any]]:
    """Turn terminal signals into one orderly Qt shutdown request."""

    requested: list[int | None] = [None]
    previous: dict[int, Any] = {}

    def request_quit(signum: int, _frame: FrameType | None) -> None:
        if requested[0] is None:
            requested[0] = signum
            logger.info("Received signal %s; stopping Game Optimization", signum)
        QTimer.singleShot(0, application.quit)

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, request_quit)
        except (OSError, RuntimeError, ValueError):
            logger.debug(
                "Could not install handler for signal %s",
                signal_number,
                exc_info=True,
            )
    return requested, previous


def _restore_termination_handlers(previous: dict[int, Any]) -> None:
    for signal_number, handler in previous.items():
        try:
            signal.signal(signal_number, handler)
        except (OSError, RuntimeError, ValueError):
            logger.debug(
                "Could not restore handler for signal %s",
                signal_number,
                exc_info=True,
            )


def run(argv: Sequence[str] | None = None) -> int:
    """Create Qt, expose the controller to QML, and enter the event loop.

    The privilege check deliberately happens before creating application
    directories or a Qt application instance.  Demo mode never executes shell
    commands, regardless of how this function is called.
    """

    if is_running_as_root():
        # Avoid creating a root-owned XDG log/config tree merely to report why
        # startup was refused.
        configure_logging(log_file=None)
        logger.critical(ROOT_STARTUP_ERROR)
        return 1

    configure_logging()
    mode = (
        "Demo"
        if os.environ.get("GAME_OPTIMIZATION_DEMO", "").strip() == "1"
        else "local Steam with guarded Btrfs operations"
    )
    logger.info("Starting %s %s in %s mode", APP_NAME, APP_VERSION, mode)

    bootstrap_error = ""
    try:
        bootstrap = bootstrap_flatpak_host_components()
        if bootstrap.changed:
            logger.info(
                "Installed Flatpak host components: %s",
                ", ".join(bootstrap.changed),
            )
    except HostBootstrapError as error:
        bootstrap_error = str(error)
        logger.error("Flatpak host component bootstrap failed: %s", error)

    qml_path = MAIN_QML.resolve()
    if not qml_path.is_file():
        logger.critical("Main QML document does not exist: %s", qml_path)
        return 2

    raw_arguments = list(argv) if argv is not None else list(sys.argv)
    qt_arguments, force_desktop, reset_ui_mode = _extract_runtime_options(
        raw_arguments
    )
    _set_application_metadata()

    controller: AppController | None = None
    previous_handlers: dict[int, Any] = {}
    requested_signal: list[int | None] = [None]
    try:
        application = QGuiApplication(qt_arguments)
        requested_signal, previous_handlers = _install_termination_handlers(application)
        icon = _application_icon()
        if not icon.isNull():
            application.setWindowIcon(icon)
        else:
            logger.warning("Application icon variants could not be decoded")
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(QML_DIR.resolve()))
        engine.warnings.connect(_log_qml_warnings)

        controller = AppController(
            parent=application,
            initial_interface_mode="desktop" if force_desktop else None,
            reset_ui_mode=reset_ui_mode,
        )
        if bootstrap_error:
            QTimer.singleShot(
                0,
                lambda message=bootstrap_error: controller.showToast(message, "error"),
            )
        translation_manager = TranslationManager(application, parent=application)
        translation_manager.attach_engine(engine)
        translation_manager.translationError.connect(
            lambda message: controller.showToast(message, "error")
        )
        saved_language = str(controller.settings.get("language", "en"))
        if not translation_manager.set_language(saved_language):
            logger.warning(
                "Could not activate saved language %r; trying English",
                saved_language,
            )
            translation_manager.set_language("en")
        application.aboutToQuit.connect(controller.shutdown)
        context = engine.rootContext()
        context.setContextProperty("appController", controller)
        context.setContextProperty("translationManager", translation_manager)
        context.setContextProperty(
            "gameOptimizationDebugArtwork",
            os.environ.get("GAME_OPTIMIZATION_DEBUG_ARTWORK", "").strip() == "1",
        )

        engine.load(QUrl.fromLocalFile(str(qml_path)))
        if not engine.rootObjects():
            logger.critical("QML engine failed to create a root object from %s", qml_path)
            controller.shutdown()
            return 3

        application.aboutToQuit.connect(lambda: _prepare_qml_shutdown(engine))

        exit_code = application.exec()
        if requested_signal[0] is not None:
            return 128 + requested_signal[0]
        return exit_code
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received; stopping Game Optimization")
        return 130
    except Exception:
        logger.exception("Unhandled error while starting or running the Qt application")
        return 4
    finally:
        if controller is not None:
            controller.shutdown()
        _restore_termination_handlers(previous_handlers)


__all__ = [
    "ROOT_STARTUP_ERROR",
    "_extract_runtime_options",
    "_application_icon",
    "_prepare_qml_shutdown",
    "is_running_as_root",
    "run",
]
