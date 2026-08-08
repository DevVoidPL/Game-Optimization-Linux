"""Qt-native background orchestration for read-only library scans.

Workers in this module never mutate a QML-facing model.  They return immutable
domain values through queued Qt signals and leave applying those values to the
controller, which lives on the GUI thread.
"""

from __future__ import annotations

from collections.abc import Sequence
import inspect
import logging
from pathlib import Path
from threading import Event
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot

from ..models import Game, GameStatus


logger = logging.getLogger(__name__)


class RefreshProvider(Protocol):
    def refresh(self, *args: Any, **kwargs: Any) -> Sequence[Game] | None: ...

    def list_games(self) -> Sequence[Game]: ...


class DirectoryScanner(Protocol):
    def scan(self, path: Path, *args: Any, **kwargs: Any) -> Any: ...


class _WorkerSignals(QObject):
    refreshReady = Signal(int, object)
    refreshFailed = Signal(int, str)
    sizeReady = Signal(int, str, object)
    sizeFailed = Signal(int, str, str)
    done = Signal(object)


class _RefreshWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        provider: RefreshProvider,
        cancelled: Event,
        signals: _WorkerSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._generation = generation
        self._provider = provider
        self._cancelled = cancelled
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            if self._cancelled.is_set():
                return
            refreshed = _refresh_provider(self._provider, self._cancelled)
            games = tuple(
                self._provider.list_games() if refreshed is None else refreshed
            )
            if not self._cancelled.is_set():
                self._signals.refreshReady.emit(self._generation, games)
        except Exception as error:  # provider failures must not escape Qt workers
            logger.exception("Steam library refresh failed in the background")
            if not self._cancelled.is_set():
                self._signals.refreshFailed.emit(self._generation, str(error))
        finally:
            self._signals.done.emit(self)


class _DirectorySizeWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        game: Game,
        scanner: DirectoryScanner,
        cancelled: Event,
        signals: _WorkerSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._generation = generation
        self._game = game
        self._scanner = scanner
        self._cancelled = cancelled
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            if self._cancelled.is_set():
                return
            result = _scan_directory(
                self._scanner,
                self._game.install_path,
                self._cancelled,
            )
            if not self._cancelled.is_set():
                self._signals.sizeReady.emit(
                    self._generation,
                    self._game.id,
                    result,
                )
        except Exception as error:  # one unreadable game must not stop the library
            logger.warning(
                "Exact size scan failed for %s: %s",
                self._game.id,
                error,
                exc_info=True,
            )
            if not self._cancelled.is_set():
                self._signals.sizeFailed.emit(
                    self._generation,
                    self._game.id,
                    str(error),
                )
        finally:
            self._signals.done.emit(self)


def _scan_directory(scanner: DirectoryScanner, path: Path, cancelled: Event) -> Any:
    """Call scanners with optional cooperative-cancellation conventions.

    ``DirectorySizeScanner`` owns traversal semantics.  Supporting a few
    conventional parameter names keeps the Qt bridge small and also makes it
    straightforward to supply deterministic fakes in controller tests.
    """

    method = scanner.scan
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}

    if "cancel_event" in parameters:
        return method(path, cancel_event=cancelled)
    if "cancellation_event" in parameters:
        return method(path, cancellation_event=cancelled)
    if "should_cancel" in parameters:
        return method(path, should_cancel=cancelled.is_set)
    return method(path)


def _refresh_provider(
    provider: RefreshProvider, cancelled: Event
) -> Sequence[Game] | None:
    """Pass cooperative cancellation to providers that support it."""

    method = provider.refresh
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}

    if "cancel_event" in parameters:
        return method(cancel_event=cancelled)
    if "cancellation_event" in parameters:
        return method(cancellation_event=cancelled)
    if "should_cancel" in parameters:
        return method(should_cancel=cancelled.is_set)
    return method()


class LibraryScanner(QObject):
    """Coordinate one refresh generation and its per-game size jobs."""

    scanStarted = Signal(int)
    libraryReady = Signal(int, object)
    libraryFailed = Signal(int, str)
    gameSizeStarted = Signal(int, str)
    gameSizeReady = Signal(int, str, object)
    gameSizeFailed = Signal(int, str, str)
    scanFinished = Signal(int)

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 4) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, int(max_threads)))
        self._signals = _WorkerSignals(self)
        queued = Qt.ConnectionType.QueuedConnection
        self._signals.refreshReady.connect(self._on_refresh_ready, queued)
        self._signals.refreshFailed.connect(self._on_refresh_failed, queued)
        self._signals.sizeReady.connect(self._on_size_ready, queued)
        self._signals.sizeFailed.connect(self._on_size_failed, queued)
        self._signals.done.connect(self._on_worker_done, queued)

        self._generation = 0
        self._cancelled = Event()
        self._directory_scanner: DirectoryScanner | None = None
        self._pending_sizes = 0
        self._workers: set[QRunnable] = set()
        self._closed = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def is_active(self) -> bool:
        return bool(self._workers) and not self._cancelled.is_set()

    def start(
        self,
        provider: RefreshProvider,
        *,
        directory_scanner: DirectoryScanner | None = None,
    ) -> int:
        """Start a fresh generation and cancel queued work from the old one."""

        if self._closed:
            raise RuntimeError("library scanner has been shut down")
        self._cancelled.set()
        self._pool.clear()
        self._workers.clear()
        self._generation += 1
        self._cancelled = Event()
        self._directory_scanner = directory_scanner
        self._pending_sizes = 0
        generation = self._generation
        worker = _RefreshWorker(
            generation,
            provider,
            self._cancelled,
            self._signals,
        )
        self._workers.add(worker)
        self.scanStarted.emit(generation)
        self._pool.start(worker)
        return generation

    def cancel(self) -> None:
        self._cancelled.set()
        self._pool.clear()

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Cooperatively cancel traversal and wait a bounded time for workers."""

        if self._closed:
            return True
        self._closed = True
        self.cancel()
        completed = self._pool.waitForDone(max(0, int(timeout_ms)))
        if not completed:
            logger.warning("Library workers did not stop within %d ms", timeout_ms)
        return completed

    @Slot(int, object)
    def _on_refresh_ready(self, generation: int, raw_games: object) -> None:
        if not self._accept(generation):
            return
        games = tuple(raw_games) if isinstance(raw_games, Sequence) else ()
        self.libraryReady.emit(generation, games)

        scanner = self._directory_scanner
        eligible = [
            game
            for game in games
            if isinstance(game, Game) and game.status is not GameStatus.MISSING_FILES
        ]
        if scanner is None or not eligible:
            self.scanFinished.emit(generation)
            return

        self._pending_sizes = len(eligible)
        for game in eligible:
            if not self._accept(generation):
                break
            worker = _DirectorySizeWorker(
                generation,
                game,
                scanner,
                self._cancelled,
                self._signals,
            )
            self._workers.add(worker)
            self.gameSizeStarted.emit(generation, game.id)
            self._pool.start(worker)

    @Slot(int, str)
    def _on_refresh_failed(self, generation: int, message: str) -> None:
        if not self._accept(generation):
            return
        self.libraryFailed.emit(generation, message)
        self.scanFinished.emit(generation)

    @Slot(int, str, object)
    def _on_size_ready(self, generation: int, game_id: str, result: object) -> None:
        if not self._accept(generation):
            return
        self.gameSizeReady.emit(generation, game_id, result)
        self._complete_size(generation)

    @Slot(int, str, str)
    def _on_size_failed(self, generation: int, game_id: str, message: str) -> None:
        if not self._accept(generation):
            return
        self.gameSizeFailed.emit(generation, game_id, message)
        self._complete_size(generation)

    @Slot(object)
    def _on_worker_done(self, worker: object) -> None:
        self._workers.discard(worker)  # type: ignore[arg-type]

    def _complete_size(self, generation: int) -> None:
        self._pending_sizes = max(0, self._pending_sizes - 1)
        if self._pending_sizes == 0:
            self.scanFinished.emit(generation)

    def _accept(self, generation: int) -> bool:
        return (
            not self._closed
            and generation == self._generation
            and not self._cancelled.is_set()
        )


__all__ = ["DirectoryScanner", "LibraryScanner", "RefreshProvider"]
