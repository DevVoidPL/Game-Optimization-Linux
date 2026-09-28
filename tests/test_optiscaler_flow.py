"""OptiScaler: one-button online install, conflicts, rate limit, moved games,
versions and the Couch status handling. Everything runs in temporary dirs."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import time
from urllib.error import HTTPError

import pytest
from PySide6.QtCore import QCoreApplication, QUrl

from game_optimization_linux import config
from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.optiscaler import OptiScalerError
from game_optimization_linux.services.optiscaler_online import (
    CachedOptiScalerArchive,
    OptiScalerNetworkError,
    OptiScalerRateLimitError,
    OptiScalerRelease,
    OptiScalerReleaseAsset,
    OptiScalerReleaseClient,
    compare_installed_release,
    version_key,
)
from tests.test_optiscaler import _archive, _game, _hash, setup_service  # noqa: F401

RELEASE = OptiScalerRelease(
    tag_name="v0.7.7",
    version="0.7.7",
    html_url="https://github.com/optiscaler/OptiScaler/releases/tag/v0.7.7",
    published_at="2026-01-01T00:00:00Z",
    asset=OptiScalerReleaseAsset(
        "OptiScaler_v0.7.7.7z",
        "https://github.com/optiscaler/OptiScaler/releases/download/v0.7.7/OptiScaler_v0.7.7.7z",
        1,
    ),
)


class FakeClient:
    """Release client double: no network, archive from a temp file."""

    rate_limited_until = 0.0

    def __init__(self, archive: Path, error: Exception | None = None) -> None:
        self.archive, self.error, self.calls = archive, error, 0

    def latest_release(self, *, channel="stable", force_refresh=False, allow_stale_cache=True):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RELEASE

    def ensure_archive(self, release, progress=None):
        if progress:
            progress(1, 1)
        return CachedOptiScalerArchive(self.archive, _hash(self.archive), self.archive.stat().st_size, release, True)

    def cached_release(self, channel="stable"):
        return None

    def cached_archive(self, release):
        return None


def _controller(service, game, tmp_path, client):
    return AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"), optiscaler_service=service,
        optiscaler_release_client=client, initial_games=(game,), demo_mode=True, auto_refresh=False,
    )


def _run(controller, game, confirmed: str = "") -> dict:
    exe = "Binaries/Win64/TestGame-Win64-Shipping.exe"
    assert controller.startOptiScalerInstall(game.id, exe, "auto", "auto", confirmed, False, {})
    deadline = time.monotonic() + 20
    while controller._optiscaler_jobs and time.monotonic() < deadline:
        controller._poll_optiscaler_jobs()
        time.sleep(0.02)
    return controller._optiscaler_controller.getOptiScalerStatus(game.id)


def test_conflict_needs_exact_confirmation_then_backs_up(setup_service, tmp_path) -> None:
    service, game, archive, root = setup_service
    original = root / "Binaries" / "Win64" / "dxgi.dll"
    original.write_bytes(b"original proxy")
    controller = _controller(service, game, tmp_path, FakeClient(archive))
    try:
        status = _run(controller, game)                     # no confirmation
        conflict = status["operationConflict"]
        assert conflict["kind"] == "confirmation_required" and conflict["files"][0]["relativePath"] == "dxgi.dll"
        assert original.read_bytes() == b"original proxy" and not status["installed"]
        status = _run(controller, game, "not-the-digest")    # wrong confirmation
        assert original.read_bytes() == b"original proxy" and not status["installed"]
        status = _run(controller, game, conflict["digest"])  # exact confirmation
        assert status["installed"] is True and original.read_bytes() == b"optiscaler proxy"
        backup = service.backup_root(status["appId"], status["manifestId"]) / "dxgi.dll"
        assert backup.read_bytes() == b"original proxy"
    finally:
        controller.shutdown()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OptiScalerRateLimitError("x", retry_at=time.time() + 600), "GitHub API rate limit reached; try again in 10 min"),
        (OptiScalerNetworkError("down"), "Could not reach GitHub. Check the internet connection and try again"),
    ],
)
def test_online_failure_is_reported_and_never_uses_a_local_archive(setup_service, tmp_path, error, expected) -> None:
    service, game, archive, _root = setup_service
    controller = _controller(service, game, tmp_path, FakeClient(archive, error))
    controller._local_file_argument = lambda _value: pytest.fail("fell back to a local archive")
    try:
        status = _run(controller, game)
        assert status["onlineError"] == expected and status["operationError"] == expected
        assert not status["installed"]
    finally:
        controller.shutdown()


def test_github_403_rate_limit_is_recognised_and_not_retried(tmp_path) -> None:
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "rate limit",
                        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 600)}, None)

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    for _ in range(2):
        with pytest.raises(OptiScalerRateLimitError) as raised:
            client.latest_release(force_refresh=True)
    assert len(calls) == 1 and 9 <= raised.value.retry_minutes(time.time()) <= 10


def test_moved_game_is_detected_and_changed_files_are_inconsistent(setup_service, tmp_path) -> None:
    service, game, archive, root = setup_service
    installed = service.install(game, archive, allow_replace_conflicts=True)
    moved_root = tmp_path / "other-library" / "game"
    moved_root.parent.mkdir()
    root.rename(moved_root)
    moved = _game(moved_root)
    manifest = service._load_manifest(installed)
    assert service.locate_installation(moved, manifest)["state"] == "moved"
    with pytest.raises(OptiScalerError, match="moved"):
        service.remove(moved)                                  # never a raw [Errno 2]
    (moved_root / "Binaries" / "Win64" / "dxgi.dll").write_bytes(b"changed")
    assert service.locate_installation(moved, manifest)["state"] == "inconsistent"
    with pytest.raises(OptiScalerError, match="inconsistent"):
        service.remove(moved)
    assert (moved_root / "Binaries" / "Win64" / "dxgi.dll").read_bytes() == b"changed"   # nothing removed
    (moved_root / "Binaries" / "Win64" / "dxgi.dll").write_bytes(b"optiscaler proxy")
    relocated = service.relocate_installation(moved)
    assert Path(relocated.install_directory) == moved_root / "Binaries" / "Win64"
    assert service.status(moved)["installLocation"]["state"] == "current"


def test_versions_and_channels() -> None:
    assert version_key("0.9.4") < version_key("10.0.0-pre1") < version_key("10.0.0")
    assert compare_installed_release("10.0.0-pre1", "edge", "0.9.4", "stable") == "other_channel"
    assert compare_installed_release("0.9.4", "stable", "0.9.5", "stable") == "older"
    assert compare_installed_release("10.0.0-pre1", "edge", "10.0.0-pre2", "edge") == "older"
    assert compare_installed_release("v0.9.4", "stable", "0.9.4", "stable") == "same"


def test_couch_status_failure_ends_checking_and_signal_is_qml_readable() -> None:
    from PySide6.QtCore import QMetaMethod  # noqa: F401
    from tests.test_couch_details import GAME, _call, _load

    engine, details = _load(config.QML_DIR / "couch" / "CouchGameDetails.qml",
                            {"controller": {"selectedGame": GAME}, "width": 1920, "height": 1080})
    try:
        details.setProperty("launcherIntegrationSupported", True)
        _call(details, "applyOptiScalerStatus", {"success": True, "loading": True, "refreshing": True})
        assert details.property("optiScalerBusy") is True
        _call(details, "applyOptiScalerStatus", {"success": False, "error": "Could not reach GitHub. Check the internet connection and try again"})
        assert details.property("optiScalerBusy") is False
        assert "GitHub" in str(details.property("optiScalerErrorText"))
    finally:
        details.deleteLater()
        QCoreApplication.processEvents()
        del engine
    meta = AppController.staticMetaObject
    signature = next(bytes(meta.method(i).methodSignature()).decode() for i in range(meta.methodCount())
                     if bytes(meta.method(i).name()) == b"optiScalerStatusChanged")
    assert signature == "optiScalerStatusChanged(QString,QVariantMap)"


@pytest.mark.skipif(os.environ.get("GOL_NETWORK_TESTS") != "1", reason="real download; set GOL_NETWORK_TESTS=1")
def test_real_official_release_downloads_verifies_and_extracts(tmp_path) -> None:
    client = OptiScalerReleaseClient(tmp_path / "cache")
    try:
        release = client.latest_release(channel="stable", force_refresh=True)
    except OptiScalerRateLimitError:
        pytest.skip("GitHub API rate limit reached")
    archive = client.ensure_archive(release)
    assert archive.sha256 == sha256(archive.path.read_bytes()).hexdigest()
    from game_optimization_linux.services.archive_reader import open_archive

    destination = tmp_path / "extracted"
    destination.mkdir()
    open_archive(archive.path).extract_to(destination)
    assert any(path.name.casefold() == "optiscaler.dll" for path in destination.rglob("*"))
