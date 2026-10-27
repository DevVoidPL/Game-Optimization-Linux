"""OptiScaler and ReShade share dxgi.dll; ownership is decided by recorded hashes.

Synthetic game trees only. ReShade's GameOpti record is written the way
PostProcessingService.apply() stores it (module, moduleHash, ownedHashes).
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from game_optimization_linux.services import OptiScalerConflictError
from game_optimization_linux.services.post_processing import MANAGED, PostProcessingService, profile_path

from tests.test_optiscaler import setup_service  # noqa: F401 - shared synthetic fixture


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _install_reshade_over(service, game, directory: Path, *, proxy=b"ReShade64 synthetic proxy") -> dict:
    """ReShade takes the shared proxy (as after a manual OptiScaler removal or older build)."""
    (directory / "dxgi.dll").write_bytes(proxy)
    (directory / "ReShade.ini").write_bytes(b"[GENERAL]\nOwnerSetting=kept\n")
    (directory / MANAGED).mkdir(exist_ok=True)
    (directory / MANAGED / "Preset.ini").write_bytes(b"owner preset")
    owned = {name: _hash(directory / name) for name in ("dxgi.dll", "ReShade.ini", f"{MANAGED}/Preset.ini")}
    state = {"backend": "reshade", "phase": "installed", "directory": str(directory), "module": "dxgi.dll",
             "moduleHash": owned["dxgi.dll"], "ownedHashes": owned, "api": "d3d12",
             "executable": "Binaries/Win64/TestGame-Win64-Shipping.exe", "version": "6.8.0"}
    path = profile_path(game.profile_key, service.profile_repository.root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    return state


def _snapshot(*roots: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for root in roots for p in sorted(root.rglob("*")) if p.is_file()}


def test_reshade_owned_proxy_is_left_when_optiscaler_is_removed(setup_service, monkeypatch):
    service, game, archive, root = setup_service
    monkeypatch.setattr("game_optimization_linux.providers.steam.steam_update_activity", lambda game: {"blocked": False})
    service.install(game, archive)
    directory = root / "Binaries" / "Win64"
    _install_reshade_over(service, game, directory)
    reshade_proxy = (directory / "dxgi.dll").read_bytes()
    profile = service.profile_repository.load("224760")
    status = service.ownership_status(profile, service._load_manifest(profile))
    assert status["state"] == "displaced" and status["owner"] == "reshade" and status["canRemove"]

    removed = service.remove(game)
    assert removed.installation_state == "removed"
    assert (directory / "dxgi.dll").read_bytes() == reshade_proxy  # never deleted as an OptiScaler file
    assert (directory / "ReShade.ini").read_bytes() == b"[GENERAL]\nOwnerSetting=kept\n"
    assert not (directory / "plugins" / "helper.dll").exists() and not (directory / "OptiScaler.ini").exists()
    manifest = service._load_manifest(removed)
    assert manifest["last_removal"]["left_to_other_integrations"] == [
        {"relative_path": "dxgi.dll", "sha256": _hash(directory / "dxgi.dll"), "owner": "reshade"}]
    after = service.ownership_status(removed, manifest)
    assert after["state"] == "removed" and not after["canRemove"] and after["ownerName"] == "ReShade"

    # Removing ReShade later moves only its own files aside; nothing resurrects OptiScaler.
    post = PostProcessingService(service)
    post.remove(game)
    assert not (directory / "dxgi.dll").exists() and not (directory / "OptiScaler.ini").exists()
    assert not (directory / "plugins" / "helper.dll").exists()
    history = Path(json.loads(profile_path("224760", service.profile_repository.root).read_text())["preservedDirectory"])
    assert (history / "dxgi.dll").read_bytes() == reshade_proxy
    assert (history / "ReShade.ini").read_bytes() == b"[GENERAL]\nOwnerSetting=kept\n"
    assert service.profile_repository.load("224760").installation_state == "removed"


def test_genuinely_foreign_modified_proxy_still_blocks_removal(setup_service):
    service, game, archive, root = setup_service
    service.install(game, archive)
    directory = root / "Binaries" / "Win64"
    _install_reshade_over(service, game, directory)
    (directory / "dxgi.dll").write_bytes(b"third-party loader, recorded by nobody")
    before = _snapshot(root, service.data_root)
    with pytest.raises(OptiScalerConflictError, match="modified immutable managed file blocks removal: dxgi.dll"):
        service.remove(game)
    assert _snapshot(root, service.data_root) == before
    profile = service.profile_repository.load("224760")
    status = service.ownership_status(profile, service._load_manifest(profile))
    assert status["state"] == "modified" and status["owner"] == "foreign" and not status["canRemove"]


def test_restoring_an_original_over_reshade_is_refused_with_the_safe_action(setup_service):
    service, game, archive, root = setup_service
    directory = root / "Binaries" / "Win64"
    (directory / "dxgi.dll").write_bytes(b"original game dxgi")
    service.install(game, archive, allow_replace_conflicts=True)
    _install_reshade_over(service, game, directory)
    before = _snapshot(root, service.data_root)
    with pytest.raises(OptiScalerConflictError, match="now owned by ReShade.*Turn post-processing Off"):
        service.remove(game)
    assert _snapshot(root, service.data_root) == before
    profile = service.profile_repository.load("224760")
    status = service.ownership_status(profile, service._load_manifest(profile))
    assert status["state"] == "displaced" and not status["canRemove"] and "Turn post-processing Off" in status["removeBlocker"]


def test_failed_coordinated_removal_rolls_back_files_manifest_and_profile(setup_service, monkeypatch):
    service, game, archive, root = setup_service
    installed = service.install(game, archive)
    directory = root / "Binaries" / "Win64"
    _install_reshade_over(service, game, directory)
    before = _snapshot(root, service.data_root, service.profile_repository.root)
    original_save = service.profile_repository.save
    def fail_save(profile):
        if profile.installation_state == "removed":
            raise OSError("synthetic profile write failure")
        return original_save(profile)
    monkeypatch.setattr(service.profile_repository, "save", fail_save)
    with pytest.raises(OSError, match="synthetic profile write failure"):
        service.remove(game)
    assert _snapshot(root, service.data_root, service.profile_repository.root) == before
    assert service.profile_repository.load(installed.app_id).installation_state == "installed"


def test_status_reports_the_cross_integration_state(setup_service):
    service, game, archive, root = setup_service
    directory = root / "Binaries" / "Win64"
    service.install(game, archive)
    data = service.status(game)
    assert data["ownership"]["state"] == "installed" and data["ownership"]["owner"] == "optiscaler"
    service.remove(game)
    data = service.status(game)
    assert data["installationState"] == "removed" and not data["installed"]
    assert data["ownership"] | {"liveHash": ""} == {"state": "removed", "proxy": "dxgi.dll", "liveHash": "", "owner": "absent",
                                                   "ownerName": "", "canRemove": False, "removeBlocker": "", "leftoverFiles": []}
    _install_reshade_over(service, game, directory)
    data = service.status(game)
    assert data["ownership"]["state"] == "removed" and data["ownership"]["ownerName"] == "ReShade"
    assert data["manifestId"] and not data["ownership"]["canRemove"]  # stale manifest is history only
