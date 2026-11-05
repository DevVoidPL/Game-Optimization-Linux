"""Optional native Desktop frontend; Qt remains the backend event dispatcher."""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Sequence


def dispatch_qt(application):
    """Deliver timers, queued worker signals and deferred QObject deletion."""
    from PySide6.QtCore import QCoreApplication, QEvent

    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return True


def run(argv: Sequence[str] | None = None) -> int:
    """Run GTK on the main thread and dispatch backend Qt events from GLib."""
    if getattr(os, "geteuid", lambda: -1)() == 0:
        print("Game Optimization Linux refuses to start with root privileges.", file=sys.stderr)
        return 1
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib
        from PySide6.QtWidgets import QApplication
        from ..config import APP_ID, APP_NAME, APP_VERSION, ORGANIZATION_NAME, ORGANIZATION_DOMAIN
        from ..controllers import AppController
        from ..logging_config import configure_logging
        from ..services.host_bootstrap import HostBootstrapError, bootstrap_flatpak_host_components
        from .window import DesktopWindow
    except (ImportError, ValueError) as error:
        print(f"GTK frontend requires GTK4, libadwaita >= 1.4, PyGObject and PySide6: {error}", file=sys.stderr)
        return 2
    if Adw.get_major_version() == 1 and Adw.get_minor_version() < 4:
        print("GTK frontend requires libadwaita >= 1.4", file=sys.stderr)
        return 2
    configure_logging()
    arguments = list(argv) if argv is not None else list(sys.argv)
    unknown = [arg for arg in arguments[1:] if arg not in {"--desktop", "--reset-ui-mode"}]
    if unknown:
        print(f"Unsupported GTK arguments: {' '.join(unknown)}", file=sys.stderr)
        return 2
    # QApplication is required by the unchanged backend (clipboard and workers).
    # Do not load an XCB/Wayland Qt platform beside GTK's native display.
    qt = QApplication.instance() or QApplication([arguments[0], "-platform", "offscreen"])
    qt.setApplicationName(APP_NAME)
    qt.setApplicationVersion(APP_VERSION)
    qt.setOrganizationName(ORGANIZATION_NAME)
    qt.setOrganizationDomain(ORGANIZATION_DOMAIN)
    qt.setQuitOnLastWindowClosed(False)
    application = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    controller = None
    timer = None
    previous = {}
    requested_signal = []
    windows = []
    try:
        bootstrap_error = ""
        try:
            bootstrap_flatpak_host_components()
        except HostBootstrapError as error:
            bootstrap_error = str(error)
        controller = AppController(parent=qt, initial_interface_mode="desktop", reset_ui_mode="--reset-ui-mode" in arguments)

        def activate(app):
            if not windows:
                windows.append(DesktopWindow(app, controller))
                if bootstrap_error:
                    windows[0].toast(bootstrap_error)
            windows[0].present()

        def dispatch():
            return dispatch_qt(qt)

        def terminate(number, _frame):
            requested_signal.append(number)
            GLib.idle_add(application.quit)

        application.connect("activate", activate)
        timer = GLib.timeout_add(10, dispatch)
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, terminate)
        result = application.run([arguments[0]])
        return 128 + requested_signal[0] if requested_signal else result
    except Exception:
        logging.getLogger(__name__).exception("GTK frontend failed")
        return 4
    finally:
        if timer is not None:
            GLib.source_remove(timer)
        if controller is not None:
            controller.shutdown()
        qt.processEvents()
        for number, handler in previous.items():
            signal.signal(number, handler)