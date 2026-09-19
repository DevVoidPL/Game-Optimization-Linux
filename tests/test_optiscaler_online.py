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
    OFFICIAL_NIGHTLY_RELEASES_URL,
    OFFICIAL_NIGHTLY_REPOSITORY,
    OptiScalerDownloadError,
    OptiScalerMetadataError,
    OptiScalerNetworkError,
    OptiScalerReleaseClient,
    parse_latest_stable_release,
    parse_release,
)


class _Response(BytesIO):
    def __init__(self, body: bytes, *, url: str, status: int = 200) -> None:
        super().__init__(body)
        self.status = status
        self._url = url

    def geturl(self) -> str:
        return self._url


def _archive_bytes(*, traversal: bool = False, fsr4: bool = False) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("OptiScaler/OptiScaler.dll", b"dll")
        archive.writestr(
            "OptiScaler/OptiScaler.ini",
            (
                b"[FSR]\nFsr4Update=auto\nFsr4ForceEnableInt8=auto\n"
                b"FsrAgilitySDKUpgrade=auto\nFsr4EnableWatermark=auto\n"
                b"[Upscalers]\nDx11Upscaler=auto\nDx12Upscaler=auto\n"
                b"VulkanUpscaler=auto\n"
                if fsr4
                else b"[OptiScaler]\n"
            ),
        )
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
                "body": "Bundled FFX 2.3 SDK with FSR 4.1.1.",
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
    archive = _archive_bytes(fsr4=True)
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
    assert release.fidelityfx_upscaler_version == "4.1.1"
    assert first.path.is_file()
    assert first.sha256 == sha256(archive).hexdigest()
    assert first.from_cache is False
    assert second.from_cache is True
    assert len(calls) == 2
    assert client.cached_release() is not None
    assert client.cached_archive(release) is not None


def test_edge_discovery_uses_selected_asset_url_and_downloads_it(
    tmp_path: Path,
) -> None:
    archive = _archive_bytes()
    payload = [
        {
            "tag_name": "nightly-20260918",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260918",
            "published_at": "2026-09-18T08:51:17Z",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_v10.0.0-pre1_20260918.zip",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.zip",
                    "size": len(archive),
                    "digest": "sha256:" + sha256(archive).hexdigest(),
                }
            ],
        }
    ]
    requests: list[str] = []

    def opener(request, **_kwargs):
        requests.append(request.full_url)
        if request.full_url == OFFICIAL_NIGHTLY_RELEASES_URL:
            return _Response(json.dumps(payload).encode(), url=request.full_url)
        return _Response(
            archive,
            url=payload[0]["assets"][0]["browser_download_url"],
        )

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    release = client.latest_release(channel="edge", force_refresh=True)
    cached = client.ensure_archive(release)

    assert release.asset.download_url == payload[0]["assets"][0]["browser_download_url"]
    assert cached.path.is_file()
    assert cached.sha256 == sha256(archive).hexdigest()
    assert requests == [OFFICIAL_NIGHTLY_RELEASES_URL, release.asset.download_url]


def test_edge_download_retries_transient_timeout_without_switching_release(
    tmp_path: Path,
) -> None:
    archive = _archive_bytes()
    release_payload = [
        {
            "tag_name": "nightly-20260918",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260918",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_v10.0.0-pre1_20260918.zip",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.zip",
                    "size": len(archive),
                }
            ],
        }
    ]
    attempts = 0

    def opener(request, **_kwargs):
        nonlocal attempts
        if request.full_url.endswith("/releases"):
            return _Response(json.dumps(release_payload).encode(), url=request.full_url)
        attempts += 1
        if attempts == 1:
            raise TimeoutError("synthetic timeout")
        return _Response(
            archive,
            url="https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.zip",
        )

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    release = client.latest_release(channel="edge", force_refresh=True)
    cached = client.ensure_archive(release)

    assert attempts == 2
    assert cached.release.asset.download_url == release.asset.download_url


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


def test_stable_selects_latest_published_qualified_release_when_payload_is_shuffled() -> None:
    payload = [
        {
            "tag_name": "v0.9.3",
            "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v0.9.3",
            "published_at": "2026-06-18T21:18:06Z",
            "prerelease": False,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_0.9.3.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler/releases/download/v0.9.3/OptiScaler_0.9.3.7z",
                    "size": 10,
                }
            ],
        },
        {
            "tag_name": "v0.9.4",
            "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v0.9.4",
            "published_at": "2026-07-18T21:46:07Z",
            "prerelease": False,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_0.9.4.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler/releases/download/v0.9.4/OptiScaler_0.9.4.7z",
                    "size": 10,
                }
            ],
        },
    ]

    assert parse_latest_stable_release(payload).version == "0.9.4"


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


def test_official_edge_channel_accepts_only_official_prerelease_assets() -> None:
    payload = [
        {
            "tag_name": "nightly-20260918",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260918",
            "draft": False,
            "prerelease": True,
            "body": "FSR 4.1.1 edge",
            "assets": [
                {
                    "name": "OptiScaler_v10.0.0-pre1_20260918.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.7z",
                    "size": 42,
                }
            ],
        }
    ]
    release = parse_release(payload, channel="edge")
    assert release.channel == "edge"
    assert release.repository == OFFICIAL_NIGHTLY_REPOSITORY
    assert release.version == "10.0.0-pre1"
    assert release.fidelityfx_upscaler_version == "4.1.1"

    payload[0]["assets"][0]["browser_download_url"] = (
        "https://github.com/optiscaler/OptiScaler/releases/download/"
        "nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.7z"
    )
    with pytest.raises(OptiScalerMetadataError):
        parse_release(payload, channel="edge")


def test_edge_selects_latest_qualified_nightly_not_old_main_repo_prerelease() -> None:
    payload = [
        {
            "tag_name": "nightly-20260917",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260917",
            "published_at": "2026-09-17T09:18:40Z",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_v10.0.0-pre1_20260917.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260917/OptiScaler_v10.0.0-pre1_20260917.7z",
                    "size": 10,
                }
            ],
        },
        {
            "tag_name": "nightly-20260918",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260918",
            "published_at": "2026-09-18T08:51:17Z",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_v10.0.0-pre1_20260918.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.7z",
                    "size": 11,
                }
            ],
        },
    ]

    release = parse_release(payload, channel="edge")

    assert release.tag_name == "nightly-20260918"
    # Selection uses the upstream publication timestamp, not list position.
    assert release.version == "10.0.0-pre1"


def test_edge_rejects_old_main_repository_prerelease_payload() -> None:
    payload = [
        {
            "tag_name": "v0.7-old_nightly",
            "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v0.7-old_nightly",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_v0.7.7-pre13_20250731.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler/releases/download/v0.7-old_nightly/OptiScaler_v0.7.7-pre13_20250731.7z",
                    "size": 10,
                }
            ],
        }
    ]

    with pytest.raises(
        OptiScalerMetadataError,
        match="latest edge OptiScaler releases have no supported",
    ):
        parse_release(payload, channel="edge")


def test_edge_reports_no_release_when_nightly_has_no_qualifying_asset() -> None:
    payload = [
        {
            "tag_name": "nightly",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly",
            "prerelease": True,
            "draft": False,
            "assets": [],
        }
    ]

    with pytest.raises(OptiScalerMetadataError, match="no supported ZIP or 7z asset"):
        parse_release(payload, channel="edge")


def test_channel_discovery_uses_separate_upstream_endpoints(tmp_path: Path) -> None:
    archive = _archive_bytes()
    requests: list[str] = []

    def opener(request, **_kwargs):
        requests.append(request.full_url)
        return _Response(_metadata(archive), url=request.full_url)

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    client.latest_release(channel="stable", force_refresh=True)
    # This response is intentionally stable-shaped; endpoint selection is the
    # behavior under test, while parser rejection protects channel isolation.
    with pytest.raises(OptiScalerMetadataError):
        client.latest_release(channel="edge", force_refresh=True)

    assert requests[0] == "https://api.github.com/repos/optiscaler/OptiScaler/releases"
    assert requests[1] == OFFICIAL_NIGHTLY_RELEASES_URL


def test_stable_and_edge_metadata_caches_are_separate(tmp_path: Path) -> None:
    archive = _archive_bytes()
    stable_payload = json.loads(_metadata(archive))
    edge_payload = [
        {
            "tag_name": "nightly-20260918",
            "html_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260918",
            "published_at": "2026-09-18T08:51:17Z",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "OptiScaler_v10.0.0-pre1_20260918.7z",
                    "browser_download_url": "https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260918/OptiScaler_v10.0.0-pre1_20260918.7z",
                    "size": 10,
                }
            ],
        }
    ]

    def opener(request, **_kwargs):
        payload = (
            stable_payload
            if request.full_url == "https://api.github.com/repos/optiscaler/OptiScaler/releases"
            else edge_payload
        )
        return _Response(json.dumps(payload).encode(), url=request.full_url)

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    assert client.latest_release(channel="stable", force_refresh=True).version == "1.2.3"
    assert client.latest_release(channel="edge", force_refresh=True).version == "10.0.0-pre1"
    assert client.cached_release("stable").version == "1.2.3"
    assert client.cached_release("edge").version == "10.0.0-pre1"
    assert (tmp_path / "cache/latest-stable.json").is_file()
    assert (tmp_path / "cache/latest-edge.json").is_file()


def test_archive_redirect_outside_explicit_github_asset_hosts_is_rejected(
    tmp_path: Path,
) -> None:
    archive = _archive_bytes()

    def opener(request, **_kwargs):
        if request.full_url.endswith("/releases"):
            return _Response(_metadata(archive), url=request.full_url)
        return _Response(
            archive,
            url="https://raw.githubusercontent.com/optiscaler/OptiScaler/archive.zip",
        )

    client = OptiScalerReleaseClient(tmp_path / "cache", opener=opener)
    with pytest.raises(OptiScalerDownloadError, match="redirected outside"):
        client.ensure_archive(client.latest_release())


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
    archive = _archive_bytes(fsr4=True)

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
        assert controller.installAndConfigureOnlineOptiScaler(
            game.id,
            plan["executable"],
            "dxgi.dll",
            "install",
            False,
            False,
            {
                "fsr4Mode": "force_int8",
                "effectiveFsr4Mode": "force_int8",
                "fsrAgilitySdkUpgrade": False,
                "fsr4Watermark": False,
                "dx11Upscaler": "auto",
                "dx12Upscaler": "auto",
                "vulkanUpscaler": "auto",
            },
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
        assert installed["sourceIdentity"] == "official_optiscaler"
        assert installed["fidelityFxUpscalerVersion"] == "4.1.1"
        ini = executable.parent / "OptiScaler.ini"
        assert "Fsr4EnableWatermark=auto" in ini.read_text(encoding="utf-8")
        assert "Fsr4ForceEnableInt8=true" in ini.read_text(encoding="utf-8")

        ini.write_text(
            ini.read_text(encoding="utf-8").replace(
                "Fsr4EnableWatermark=auto", "Fsr4EnableWatermark=true"
            ),
            encoding="utf-8",
        )
        service.verify(game)
        assert controller.installOnlineOptiScaler(
            game.id,
            plan["executable"],
            "dxgi.dll",
            "repair",
            True,
            False,
        ) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_jobs:
                break
            time.sleep(0.01)
        assert "Fsr4EnableWatermark=auto" in ini.read_text(encoding="utf-8")
        assert "Fsr4ForceEnableInt8=true" in ini.read_text(encoding="utf-8")
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
