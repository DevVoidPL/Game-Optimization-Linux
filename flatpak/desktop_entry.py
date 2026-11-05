"""Compiled Desktop-only entry point; the source is never installed."""
import os
import sys

from game_optimization_linux.gtk_frontend import run
from game_optimization_linux.runner import main as runner_main


def main():
    arguments = list(sys.argv)
    if "--release-self-test" in arguments:
        from game_optimization_linux.config import TRANSLATIONS_DIR
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        import game_optimization_linux.gtk_frontend.details
        if not any(TRANSLATIONS_DIR.glob("*.qm")):
            raise RuntimeError("Missing compiled translation catalogs")
        if Gtk.get_major_version() != 4 or (Adw.get_major_version(), Adw.get_minor_version()) < (1, 4):
            raise RuntimeError("Unsupported GTK/libadwaita runtime")
        print("Desktop runtime imports and resources OK")
        return 0
    if os.path.basename(arguments[0]) == "game-optimization-run" or "--release-runner" in arguments:
        arguments = [argument for argument in arguments if argument != "--release-runner"]
        return runner_main(arguments[1:])
    arguments = [argument for argument in arguments if argument != "--gtk"]
    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())