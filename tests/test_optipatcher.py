from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from game_optimization_linux.services.optipatcher_online import (
    OptiPatcherOnlineError,
    OptiPatcherReleaseClient,
)


def test_latest_release_selects_highest_official_stable_asi() -> None:
    payload = [
        {
            "tag_name": "v0.40",
            "html_url": "https://github.com/optiscaler/OptiPatcher/releases/tag/v0.40",
            "prerelease": False,
            "draft": False,
            "assets": [{"name": "OptiPatcher_v0.40.asi", "browser_download_url": "https://github.com/optiscaler/OptiPatcher/releases/download/v0.40/OptiPatcher_v0.40.asi", "size": 2}],
        },
        {
            "tag_name": "v0.41",
            "html_url": "https://github.com/optiscaler/OptiPatcher/releases/tag/v0.41",
            "prerelease": False,
            "draft": False,
            "assets": [{"name": "OptiPatcher_v0.41.asi", "browser_download_url": "https://github.com/optiscaler/OptiPatcher/releases/download/v0.41/OptiPatcher_v0.41.asi", "size": 2}],
        },
    ]
    release = OptiPatcherReleaseClient.parse_releases(payload)
    assert release.version == "0.41"
    assert release.asset_name == "OptiPatcher_v0.41.asi"


def test_release_parser_rejects_unknown_future_asset() -> None:
    with pytest.raises(OptiPatcherOnlineError):
        OptiPatcherReleaseClient.parse_releases(
            [{"tag_name": "v0.42", "prerelease": False, "draft": False, "assets": [{"name": "OptiPatcher.zip", "browser_download_url": "https://example.com/a"}]}]
        )


def test_asset_download_is_checksum_verified_and_cached(tmp_path: Path) -> None:
    payload = b"asi"
    url = "https://github.com/optiscaler/OptiPatcher/releases/download/v0.41/OptiPatcher_v0.41.asi"
    release = OptiPatcherReleaseClient.parse_releases(
        [{"tag_name": "v0.41", "html_url": "", "prerelease": False, "draft": False, "assets": [{"name": "OptiPatcher_v0.41.asi", "browser_download_url": url, "size": len(payload), "digest": f"sha256:{sha256(payload).hexdigest()}"}]}]
    )

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            result, self.done = (payload, True) if not getattr(self, "done", False) else (b"", True)
            return result

    client = OptiPatcherReleaseClient(tmp_path, opener=lambda *_args, **_kwargs: Response())
    path, digest = client.ensure_asset(release)
    assert path.name == release.asset_name
    assert digest == sha256(payload).hexdigest()
