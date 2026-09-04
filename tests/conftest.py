"""Session-wide Qt application for the test suite.

pytest imports ``conftest.py`` before any test module, so this file decides
which Qt application object exists for the whole process.

Without it, each test module created its own application at import time with
whatever class it happened to need.  ``tests/test_btrfs_analysis.py`` is
alphabetically first and built a plain ``QCoreApplication``, which then became
the process-wide singleton.  Every later
``QGuiApplication.instance() or QGuiApplication([])`` returned that
``QCoreApplication``, so the ``or`` fallback was dead code and no GUI layer was
ever initialised.  The static ``QGuiApplication::clipboard()`` then dereferenced
that uninitialised GUI layer and the interpreter died with SIGSEGV inside
``QClipboard::setMimeData`` -- roughly 94% into the suite, in whichever test
touched the clipboard first.

Creating one ``QApplication`` up front removes the whole class of failure: the
type matches production (``app.py`` builds a ``QApplication``), and every
``instance()`` call in the test modules now reuses it instead of racing to
define the singleton.
"""

from __future__ import annotations

import os

# Must be set before the application is constructed.  setdefault keeps an
# explicit override from the caller working.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

# Module-level reference: the application must outlive every test, and must not
# be garbage collected while Qt still holds pointers into it.
QT_APPLICATION = QApplication.instance() or QApplication([os.path.basename(__file__)])

# Fail loudly and immediately if something already built a non-GUI application.
# Without this the mismatch stays invisible until whichever test first touches a
# GUI-only static, which previously meant a SIGSEGV at ~94% of the run.
assert isinstance(QT_APPLICATION, QApplication), (
    "A Qt application was created before tests/conftest.py, and it is not a "
    f"QApplication but {type(QT_APPLICATION).__name__}. GUI-only statics such "
    "as QGuiApplication.clipboard() segfault on such an instance. Find the "
    "import that constructs it and let it reuse the conftest application."
)
