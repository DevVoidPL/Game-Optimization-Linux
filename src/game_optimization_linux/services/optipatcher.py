"""OptiPatcher component management using the existing OptiScaler manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..models import Game
from .mangohud import _atomic_write
from .optiscaler import OptiScalerError, OptiScalerService
from .optipatcher_online import OptiPatcherRelease, OptiPatcherReleaseClient


class OptiPatcherService:
    COMPONENT = "optipatcher"

    def __init__(
        self,
        optiscaler: OptiScalerService,
        release_client: OptiPatcherReleaseClient,
    ) -> None:
        self.optiscaler = optiscaler
        self.release_client = release_client

    def _context(self, game: Game) -> tuple[Any, dict[str, Any], Path, Path]:
        profile = self.optiscaler.profile_repository.load(self.optiscaler.game_key(game))
        if profile.installation_state not in {"installed", "partial", "corrupt"} or not profile.manifest_id:
            raise OptiScalerError("OptiScaler must be installed before OptiPatcher")
        manifest = self.optiscaler._load_manifest(profile)
        root = Path(str(manifest.get("install_directory", ""))).resolve(strict=True)
        manifest_path = self.optiscaler.manifest_path(profile.app_id, profile.manifest_id)
        return profile, manifest, root, manifest_path

    @staticmethod
    def _component(manifest: Mapping[str, Any]) -> dict[str, Any]:
        components = manifest.get("components", {})
        item = components.get("optipatcher", {}) if isinstance(components, Mapping) else {}
        return dict(item) if isinstance(item, Mapping) else {}

    def status(self, game: Game, *, available: OptiPatcherRelease | None = None) -> dict[str, Any]:
        try:
            profile, manifest, root, _manifest_path = self._context(game)
        except OptiScalerError as error:
            return {
                "installed": False,
                "state": "blocked",
                "version": "",
                "availableVersion": available.version if available else "",
                "compatibility": "unsupported",
                "error": str(error),
            }
        component = self._component(manifest)
        relative = str(component.get("relativePath", "plugins/OptiPatcher.asi"))
        target = self.optiscaler._target(root, relative)
        installed = bool(component) and target.is_file()
        version = str(component.get("version", "")) if installed else ""
        current_hash = self.optiscaler._hash_file(target) if installed else ""
        if installed and current_hash != str(component.get("sha256", "")):
            return {
                "installed": True,
                "state": "conflict",
                "version": version,
                "availableVersion": available.version if available else "",
                "compatibility": "warning",
                "error": "OptiPatcher.asi was modified outside GameOpti",
            }
        return {
            "installed": installed,
            "state": "installed" if installed else "not_installed",
            "version": version,
            "availableVersion": available.version if available else "",
            "compatibility": (
                profile.optipatcher_compatibility if installed else "unknown"
            ),
            "requiresOptiScaler": True,
        }

    def install(self, game: Game, release: OptiPatcherRelease, asset: Path) -> dict[str, Any]:
        profile, manifest, root, manifest_path = self._context(game)
        version_parts = tuple(int(part) for part in profile.installed_version.split(".") if part.isdigit())
        if len(version_parts) < 2 or version_parts < (0, 7, 8):
            raise OptiScalerError(
                "OptiPatcher requires a confirmed OptiScaler version 0.7.8 or newer"
            )
        ini_entry = next(
            (
                item for item in manifest.get("installed_files", [])
                if isinstance(item, Mapping)
                and Path(str(item.get("relative_path", ""))).name.casefold()
                == "optiscaler.ini"
            ),
            None,
        )
        if not ini_entry:
            raise OptiScalerError("the managed OptiScaler.ini is unavailable")
        ini_path = self.optiscaler._target(root, str(ini_entry["relative_path"]))
        ini_before = ini_path.read_bytes()
        manifest_before = manifest_path.read_bytes()
        profile_path = self.optiscaler.profile_repository.path(profile.app_id)
        profile_before = profile_path.read_bytes()
        ini_text = ini_before.decode("utf-8-sig")
        plugin_match = re.search(
            r"(?ims)^\s*\[Plugins\].*?^\s*LoadAsiPlugins\s*=\s*([^\r\n;#]+)",
            ini_text,
        )
        if plugin_match is None:
            raise OptiScalerError(
                "the installed OptiScaler release does not expose Plugins.LoadAsiPlugins"
            )
        previous_load_asi = plugin_match.group(1).strip()
        component = self._component(manifest)
        target = self.optiscaler._target(root, "plugins/OptiPatcher.asi")
        if target.exists() and (
            not component
            or self.optiscaler._hash_file(target) != str(component.get("sha256", ""))
        ):
            raise OptiScalerError("OptiPatcher target is owned by another component or has been modified")
        original = target.read_bytes() if target.is_file() else None
        original_hash = sha256(original).hexdigest() if original is not None else ""
        backup_root = self.optiscaler.backup_root(profile.app_id, profile.manifest_id)
        backup_path = backup_root / "components" / "optipatcher" / "plugins" / "OptiPatcher.asi"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with asset.open("rb") as source:
                self.optiscaler._copy_atomic(source, target)
            digest = self.optiscaler._hash_file(target)
            if release.sha256 and digest != release.sha256:
                raise OptiScalerError("OptiPatcher checksum verification failed")
            self.optiscaler._apply_managed_ini(
                game,
                updates={("Plugins", "LoadAsiPlugins"): "true"},
                profile_changes={},
                settings_record={"LoadAsiPlugins": True},
            )
            updated = dict(manifest)
            components = dict(updated.get("components", {}))
            if original is not None:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_bytes(original)
            components[self.COMPONENT] = {
                "relativePath": "plugins/OptiPatcher.asi",
                "version": release.version,
                "tagName": release.tag_name,
                "repository": "optiscaler/OptiPatcher",
                "sha256": digest,
                "backupPath": str(backup_path) if original is not None else "",
                "previousLoadAsiPlugins": previous_load_asi,
                "installedAt": datetime.now(UTC).isoformat(),
            }
            updated["components"] = components
            updated["managed_components"] = sorted(components)
            _atomic_write(manifest_path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
            return self.status(game, available=release)
        except Exception:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(original)
            ini_path.write_bytes(ini_before)
            manifest_path.write_bytes(manifest_before)
            profile_path.write_bytes(profile_before)
            raise

    def remove(self, game: Game) -> dict[str, Any]:
        profile, manifest, root, manifest_path = self._context(game)
        component = self._component(manifest)
        if not component:
            return self.status(game)
        target = self.optiscaler._target(root, str(component.get("relativePath", "")))
        expected = str(component.get("sha256", ""))
        if target.is_file() and self.optiscaler._hash_file(target) != expected:
            raise OptiScalerError("modified OptiPatcher files are preserved and block removal")
        backup = Path(str(component.get("backupPath", "")))
        target_before = target.read_bytes() if target.is_file() else None
        manifest_before = manifest_path.read_bytes()
        profile_path = self.optiscaler.profile_repository.path(profile.app_id)
        profile_before = profile_path.read_bytes()
        try:
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(backup.read_bytes())
            else:
                target.unlink(missing_ok=True)
            previous_load_asi = str(component.get("previousLoadAsiPlugins", "")).strip()
            if previous_load_asi:
                self.optiscaler._apply_managed_ini(
                    game,
                    updates={("Plugins", "LoadAsiPlugins"): previous_load_asi},
                    profile_changes={},
                    settings_record={"LoadAsiPlugins": previous_load_asi},
                )
            updated = dict(manifest)
            components = dict(updated.get("components", {}))
            components.pop(self.COMPONENT, None)
            updated["components"] = components
            updated["managed_components"] = sorted(components)
            _atomic_write(manifest_path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
        except Exception:
            if target_before is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(target_before)
            manifest_path.write_bytes(manifest_before)
            profile_path.write_bytes(profile_before)
            raise
        return self.status(game)
