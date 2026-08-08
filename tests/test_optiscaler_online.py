from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from dataclasses import replace
import time
from urllib.error import URLError
from zipfile import ZipFile

import pytest

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import (
    GameExecutableResolver,
    MockTaskService,
    OptiScalerProfileRepository,
    OptiScalerService,
    ProtonTweaksRepository,
    SettingsStore,
)

from game_optimization_linux.services.optiscaler_online import (
    OptiScalerDownloadError,
    OptiScalerMetadataError,
    OptiScalerNetworkError,
    OptiScalerReleaseClient,
    parse_latest_stable_release,
)


class _Response(BytesIO):
    def __init__(self, body: bytes, *, url: str, status: int = 200) -> None:
        super().__init__(body)
        self.status = status
        self._url = url

    def geturl(self) -> str:
        return self._url


def _archive_bytes(*, traversal: bool = False) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("OptiScaler/OptiScaler.dll", b"dll")
        archive.writestr("OptiScaler/OptiScaler.ini", b"[OptiScaler]\n")
        if traversal:
            archive.writestr("../escape.dll", b"escape")
    return output.getvalue()


def _metadata(archive: bytes, *, digest: str | None = None) -> bytes:
    actual_digest = digest or "sha256:" + sha256(archive).hexdigest()
    return json.dumps(
        [
            {
                "tag_name": "v1.2.3",
                "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v1.2.3",
                "published_at": "2026-01-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "OptiScaler_v1.2.3.zip",
                        "browser_download_url": "https://github.com/optiscaler/OptiScaler/releases/download/v1.2.3/OptiScaler_v1.2.3.zip",
                        "size": len(archive),
                        "content_type": "application/zip",
                        "digest": actual_digest,
                    }
                ],
            }
        ]
    ).encode()


def test_official_release_is_downloaded_validated_and_reused_from_cache(
    tmp_path: Path,
) -> None:
    archive = _archive_bytes()
    calls: list[str] = []

    def opener(request, **_kwargs):
        calls.append(request.full_url)
        if request.full_url.endswith("/releases"):
            return _Response(_metadata(archive), url=request.full_url)
        return _Response(
            archive,
            url="https://release-assets.githubusercontent.com/github-production-release-asset/test",
        )

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    release = client.latest_release()
    first = client.ensure_archive(release)
    second = client.ensure_archive(release)

    assert release.version == "1.2.3"
    assert first.path.is_file()
    assert first.sha256 == sha256(archive).hexdigest()
    assert first.from_cache is False
    assert second.from_cache is True
    assert len(calls) == 2
    assert client.cached_release() is not None
    assert client.cached_archive(release) is not None


def test_flatpak_grants_network_for_official_release_client() -> None:
    manifest = Path(
        "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
    ).read_text(encoding="utf-8")
    assert "--share=network" in manifest
    assert "https://www.7-zip.org/a/7z2602-src.tar.xz" in manifest
    assert "game-optimization-7zz" in manifest


def test_latest_stable_ignores_prerelease_and_prefers_7z() -> None:
    payload = [
        {
            "tag_name": "v9.0.0-beta",
            "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v9.0.0-beta",
            "draft": False,
            "prerelease": True,
            "assets": [],
        },
        {
            "tag_name": "v1.0.0",
            "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v1.0.0",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "release.zip", "browser_download_url": "https://github.com/optiscaler/OptiScaler/releases/download/v1.0.0/release.zip", "size": 10},
                {"name": "release.7z", "browser_download_url": "https://github.com/optiscaler/OptiScaler/releases/download/v1.0.0/release.7z", "size": 11},
            ],
        },
    ]
    assert parse_latest_stable_release(payload).asset.name == "release.7z"


def test_network_failure_uses_only_previously_validated_stale_metadata(
    tmp_path: Path,
) -> None:
    archive = _archive_bytes()
    now = [1000.0]
    client = OptiScalerReleaseClient(
        tmp_path / "cache",
        opener=lambda request, **_kwargs: _Response(_metadata(archive), url=request.full_url),
        metadata_max_age=1,
        clock=lambda: now[0],
    )
    assert client.latest_release().stale is False
    now[0] += 10
    client._opener = lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline"))

    cached = client.latest_release()

    assert cached.source == "cache"
    assert cached.stale is True


def test_no_network_and_no_cache_is_a_clear_error(tmp_path: Path) -> None:
    client = OptiScalerReleaseClient(
        tmp_path / "cache",
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(OptiScalerNetworkError, match="official OptiScaler"):
        client.latest_release()


def test_unofficial_asset_is_rejected() -> None:
    with pytest.raises(OptiScalerMetadataError, match="supported ZIP or 7z"):
        parse_latest_stable_release(
            [
                {
                    "tag_name": "v1.0.0",
                    "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v1.0.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "OptiScaler.zip",
                            "browser_download_url": "https://mirror.invalid/OptiScaler.zip",
                            "size": 10,
                        }
                    ],
                }
            ]
        )


def test_download_rejects_path_traversal_before_cache_publish(tmp_path: Path) -> None:
    archive = _archive_bytes(traversal=True)

    def opener(request, **_kwargs):
        if request.full_url.endswith("/releases"):
            return _Response(_metadata(archive), url=request.full_url)
        return _Response(
            archive,
            url="https://release-assets.githubusercontent.com/asset",
        )

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    release = client.latest_release()
    with pytest.raises(OptiScalerDownloadError, match="invalid"):
        client.ensure_archive(release)
    assert client.cached_archive(release) is None


def test_download_rejects_github_digest_mismatch(tmp_path: Path) -> None:
    archive = _archive_bytes()
    metadata = _metadata(archive, digest="sha256:" + "0" * 64)

    def opener(request, **_kwargs):
        if request.full_url.endswith("/releases"):
            return _Response(metadata, url=request.full_url)
        return _Response(
            archive,
            url="https://release-assets.githubusercontent.com/asset",
        )

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    with pytest.raises(OptiScalerDownloadError, match="SHA-256"):
        client.ensure_archive(client.latest_release())


def test_controller_online_plan_and_install_use_validated_cache(
    tmp_path: Path,
) -> None:
    archive = _archive_bytes()

    def opener(request, **_kwargs):
        if request.full_url.endswith("/releases"):
            return _Response(_metadata(archive), url=request.full_url)
        return _Response(
            archive,
            url="https://release-assets.githubusercontent.com/asset",
        )

    root = tmp_path / "game"
    executable = root / "Example/Binaries/Win64/Example-Win64-Shipping.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic")
    game = Game(
        id="steam-224760",
        steam_app_id="224760",
        name="Example",
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=0.01,
        physical_size_gb=0.01,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
    )
    profiles = OptiScalerProfileRepository(tmp_path / "config" / "games")
    service = OptiScalerService(
        profile_repository=profiles,
        data_root=tmp_path / "data" / "games",
        executable_resolver=GameExecutableResolver(),
        process_detector=lambda _path: (),
    )
    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optiscaler_service=service,
        optiscaler_release_client=client,
        proton_tweaks_repository=ProtonTweaksRepository(tmp_path / "config" / "games"),
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    try:
        assert controller.refreshOptiScalerRelease(game.id, True) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_jobs:
                break
            time.sleep(0.01)
        status = controller.getOptiScalerStatus(game.id)
        assert status["availableVersion"] == "1.2.3"
        assert status["archiveReady"] is True
        plan = controller.inspectOnlineOptiScaler(
            game.id,
            "Example/Binaries/Win64/Example-Win64-Shipping.exe",
            "dxgi.dll",
            False,
        )
        assert plan["success"] is True
        assert plan["officialRelease"] is True
        assert controller.installOnlineOptiScaler(
            game.id,
            plan["executable"],
            "dxgi.dll",
            "install",
            False,
            False,
        ) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_jobs:
                break
            time.sleep(0.01)
        installed = controller.getOptiScalerStatus(game.id)
        assert installed["installed"] is True
        assert installed["installedVersion"] == "1.2.3"
        assert installed["onlineState"] == "installed"
    finally:
        controller.shutdown()


def test_controller_reports_online_error_without_disabling_other_features(
    tmp_path: Path,
) -> None:
    root = tmp_path / "game"
    root.mkdir()
    game = Game(
        id="steam-224760", steam_app_id="224760", name="Example",
        launcher=Launcher.STEAM, install_path=root,
        logical_size_gb=0.0, physical_size_gb=0.0,
        filesystem=FilesystemType.EXT4, compression_available=False,
    )
    client = OptiScalerReleaseClient(
        tmp_path / "cache",
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optiscaler_release_client=client, initial_games=(game,),
        demo_mode=True, auto_refresh=False,
    )
    try:
        assert controller.refreshOptiScalerRelease(game.id, True)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_jobs:
                break
            time.sleep(0.01)
        status = controller.getOptiScalerStatus(game.id)
        assert status["onlineError"]
        assert status["onlineState"] == "error"
        assert controller.games
    finally:
        controller.shutdown()
