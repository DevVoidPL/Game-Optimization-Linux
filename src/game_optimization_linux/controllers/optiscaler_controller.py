from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Future
from datetime import UTC, datetime
import logging
from pathlib import Path
from queue import Empty
import re
from threading import Event
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ..models import Game, OptiScalerProfile
from ..services import (
    OptiScalerCancelled,
    OptiScalerConflictError,
    OptiScalerError,
)
from ..services.optiscaler_online import (
    CachedOptiScalerArchive,
    OptiScalerOnlineError,
    OptiScalerRelease,
)
from ..services.optiscaler_fsr4 import (
    OptiScalerIniCapabilities,
    recommend_fsr4,
)
from ..services.optipatcher_online import OptiPatcherOnlineError

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class OptiScalerController:
    def __init__(self, app: AppController) -> None:
        self._app = app
        self._status_cache: dict[str, dict[str, Any]] = {}
        self._status_jobs: dict[str, tuple[Future[dict[str, Any]], str, int]] = {}
        self._status_generation: dict[str, int] = {}
        self._status_app_ids: dict[str, str] = {}
        self._operation_errors: dict[str, str] = {}
        self._operation_conflicts: dict[str, dict[str, str]] = {}

    @staticmethod
    def _exception_text(error: BaseException, context: str) -> str:
        detail = str(error).strip()
        if detail:
            return f"{context}: {detail}"
        return f"{context}: {type(error).__name__}"

    @staticmethod
    def _snapshot_state(result: Mapping[str, Any]) -> str:
        state = str(result.get("installationState", "")).casefold()
        if state == "installed":
            if str(result.get("manifestError", "")).strip():
                return "partial"
            return "installed"
        if state == "corrupt":
            return "corrupt"
        if state in {"partial", "restore_required"}:
            return "partial"
        if state in {"not_installed", "removed"}:
            return "not_installed"
        return "unknown"

    def _status_failure_snapshot(
        self, game_id: str, error_text: str
    ) -> dict[str, Any]:
        cached = self._status_cache.get(game_id)
        if cached is not None:
            result = dict(cached)
            result.update(
                {
                    "success": True,
                    "loading": False,
                    "refreshing": False,
                    "refreshError": error_text,
                    "snapshotFromCache": True,
                }
            )
            self._status_cache[game_id] = dict(result)
            return result
        result = {
            "success": True,
            "loading": False,
            "refreshing": False,
            "gameId": game_id,
            "snapshotState": "unknown",
            "installationState": "unknown",
            "onlineState": "unknown",
            "installed": None,
            "installedVersion": "",
            "refreshError": error_text,
            "snapshotFromCache": False,
        }
        self._status_cache[game_id] = dict(result)
        return result

    @staticmethod
    def _capabilities_from_dict(raw: object) -> OptiScalerIniCapabilities:
        value = raw if isinstance(raw, dict) else {}
        return OptiScalerIniCapabilities(
            bool(value.get("fsr4Update", False)),
            str(value.get("forceInt8Style", "none")),
            bool(value.get("agilitySdkUpgrade", False)),
            bool(value.get("watermark", False)),
            tuple(str(item) for item in value.get("dx11Upscalers", ())),
            tuple(str(item) for item in value.get("dx12Upscalers", ())),
            tuple(str(item) for item in value.get("vulkanUpscalers", ())),
            bool(value.get("known", bool(value))),
        )

    def _detected_game_context(self, app_id: str) -> dict[str, Any]:
        system = self._app._system_info
        gpu = str(
            system.get("gpu")
            or system.get("gpuModel")
            or system.get("vulkanDevice")
            or "Unknown"
        )
        graphics_api = "Unknown"
        graphics_confidence = 0.0
        game_upscaler = "Unknown"
        runtime = "Unknown"
        repository = getattr(
            self._app, "_optimization_analysis_repository", None
        )
        loader = getattr(repository, "load", None)
        if callable(loader):
            try:
                analysis = loader(app_id)
            except Exception as error:
                logger.debug("Could not read game analysis for OptiScaler: %s", error)
                analysis = None
            if analysis is not None:
                detected_api = analysis.fingerprint.graphics_api
                if detected_api.confidence >= 0.65:
                    graphics_api = detected_api.value or "Unknown"
                    graphics_confidence = detected_api.confidence
                runtime = analysis.fingerprint.runtime.value or "Unknown"
                if gpu == "Unknown" and analysis.fingerprint.system.gpu:
                    gpu = analysis.fingerprint.system.gpu
                for setting in analysis.settings.detected:
                    text = " ".join(
                        (setting.label, setting.key, setting.value)
                    )
                    match = re.search(
                        r"(?i)\b(FSR\s*[234](?:\.\d+)*|DLSS(?:\s*\d+(?:\.\d+)*)?|XeSS(?:\s*\d+(?:\.\d+)*)?)\b",
                        text,
                    )
                    if match:
                        game_upscaler = match.group(1)
                        break
        return {
            "gpu": gpu,
            "graphicsApi": graphics_api,
            "graphicsApiConfidence": graphics_confidence,
            "gameUpscaler": game_upscaler,
            "runtime": runtime,
        }

    @staticmethod
    def _profile_update_result(profile: OptiScalerProfile) -> dict[str, Any]:
        return {
            "success": True,
            "appId": profile.app_id,
            "refreshing": True,
            "channel": profile.channel,
            "executable": profile.executable,
            "fsr4Mode": profile.fsr4_mode,
            "effectiveFsr4Mode": profile.effective_fsr4_mode,
            "automaticReason": profile.automatic_reason,
            "fsrAgilitySdkUpgrade": profile.fsr_agility_sdk_upgrade,
            "fsr4Watermark": profile.fsr4_watermark,
            "dx11Upscaler": profile.dx11_upscaler,
            "dx12Upscaler": profile.dx12_upscaler,
            "vulkanUpscaler": profile.vulkan_upscaler,
            "configurationApplied": profile.configuration_applied,
            "runtimeVerified": False,
            "runtimeVerificationStatus": "not_verified",
        }

    def getOptiScalerStatus(self, game_id: str) -> dict[str, Any]:
        """Compatibility/scripting API; QML uses the non-blocking request API."""

        result = self._timed_build_optiscaler_status(game_id)
        if result.get("success"):
            result["snapshotState"] = self._snapshot_state(result)
            result["refreshError"] = ""
            result["operationError"] = self._operation_errors.get(
                str(game_id), ""
            )
            result["operationConflict"] = self._operation_conflicts.get(
                str(game_id), {}
            )
            self._status_cache[str(game_id)] = dict(result)
            return result
        error_text = str(result.get("error", "")).strip()
        if not error_text:
            error_text = "Failed to refresh OptiScaler status: no diagnostic was returned"
        return self._status_failure_snapshot(str(game_id), error_text)

    def _timed_build_optiscaler_status(self, game_id: str) -> dict[str, Any]:
        started = perf_counter()
        result = self._build_optiscaler_status(game_id)
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 50.0:
            logger.debug(
                "OptiScaler status completed in %.1f ms for gameId=%s",
                elapsed_ms,
                game_id,
            )
        return result

    def requestOptiScalerStatus(
        self, game_id: str, force_refresh: bool = False
    ) -> dict[str, Any]:
        """Return cached state immediately and refresh expensive detection off-thread."""

        key = str(game_id or "")
        if not key:
            return {"success": False, "error": "Select an available Steam game first"}
        game = self._app._resolve_game(key, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            self._status_app_ids[key] = self._app._optiscaler_service.game_key(game)
        except OptiScalerError as error:
            return {"success": False, "error": str(error)}
        cached = self._status_cache.get(key)
        if key not in self._status_jobs and (force_refresh or cached is None):
            executor = getattr(self._app, "_optiscaler_status_executor", None)
            if executor is None:
                return self.getOptiScalerStatus(key)
            generation = self._status_generation.get(key, 0)
            future = executor.submit(self._timed_build_optiscaler_status, key)
            self._status_jobs[key] = (future, key, generation)
        if cached is not None:
            result = dict(cached)
            result["loading"] = False
            result["refreshing"] = key in self._status_jobs
            return result
        return {
            "success": True,
            "loading": True,
            "refreshing": True,
            "gameId": key,
            "snapshotState": "unknown",
            "installationState": "unknown",
            "onlineState": "unknown",
            "installed": None,
            "refreshError": "",
        }

    def _invalidate_status(self, app_id: str = "") -> None:
        target = str(app_id or "")
        game_ids = (
            set(self._status_cache)
            | set(self._status_jobs)
            | set(self._status_app_ids)
        )
        for game_id in game_ids:
            status = self._status_cache.get(game_id, {})
            if (
                not target
                or game_id == target
                or self._status_app_ids.get(game_id) == target
                or str(status.get("appId", "")) == target
            ):
                self._status_generation[game_id] = (
                    self._status_generation.get(game_id, 0) + 1
                )

    def _poll_status_jobs(self) -> None:
        for key, (future, game_id, generation) in tuple(self._status_jobs.items()):
            if not future.done():
                continue
            self._status_jobs.pop(key, None)
            if generation != self._status_generation.get(key, 0):
                self.requestOptiScalerStatus(game_id, True)
                continue
            try:
                result = future.result()
            except Exception as error:
                logger.exception(
                    "Unhandled OptiScaler status refresh failure for gameId=%s",
                    game_id,
                )
                result = {
                    "success": False,
                    "error": self._exception_text(
                        error, "Failed to refresh OptiScaler status"
                    ),
                }
            if result.get("success"):
                result["snapshotState"] = self._snapshot_state(result)
                result["refreshError"] = ""
                result["operationError"] = self._operation_errors.get(game_id, "")
                result["operationConflict"] = self._operation_conflicts.get(
                    game_id, {}
                )
                self._status_cache[key] = dict(result)
            else:
                error_text = str(result.get("error", "")).strip()
                if not error_text:
                    error_text = (
                        "Failed to refresh OptiScaler status: "
                        "no diagnostic was returned"
                    )
                result = self._status_failure_snapshot(key, error_text)
            signal = getattr(self._app, "optiScalerStatusChanged", None)
            emit = getattr(signal, "emit", None)
            if callable(emit):
                emit(game_id, result)

    def _build_optiscaler_status(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            result = self._app._optiscaler_service.status(game)
            if not result.get("success"):
                detail = str(result.get("error", "")).strip()
                return {
                    "success": False,
                    "error": detail
                    or "Failed to refresh OptiScaler status: service returned no diagnostic",
                }
            channel = str(result.get("channel") or "stable")
            release = self._cached_optiscaler_release(channel)
            cached_archive = (
                self._app._cached_optiscaler_archive(release)
                if release is not None else None
            )
            app_id = self._app._optiscaler_service.game_key(game)
            installed_version = str(result.get("installedVersion", "")).strip()
            available_version = release.version if release is not None else ""
            installation_state = str(
                result.get("installationState", "not_installed")
            )
            installed = bool(result.get("installed"))
            if installation_state == "corrupt":
                online_state = "error"
            elif installed and available_version and (
                self._app._normalized_release_version(installed_version)
                != self._app._normalized_release_version(available_version)
            ):
                online_state = "update_available"
            elif installed:
                online_state = "installed"
            elif app_id in self._app._optiscaler_online_errors:
                online_state = "error"
            else:
                online_state = "not_installed"
            installed_capabilities = self._capabilities_from_dict(
                result.get("iniCapabilities")
            )
            available_capabilities = installed_capabilities
            if cached_archive is not None and (
                not installed or online_state == "update_available"
            ):
                try:
                    available_capabilities = (
                        self._app._optiscaler_service.archive_ini_capabilities(
                            cached_archive.path
                        )
                    )
                except Exception as error:
                    logger.debug(
                        "Could not inspect cached OptiScaler capabilities: %s", error
                    )
            detected = self._detected_game_context(app_id)
            optimization_profile = self._app._optimization_profile_repository.load(app_id)
            recommendation = recommend_fsr4(
                str(detected["gpu"]),
                str(detected["graphicsApi"]),
                available_capabilities,
            )
            watermark_state = str(result.get("fsr4WatermarkState", "unknown")).casefold()
            effective_watermark_labels = {
                "true": "Enabled",
                "false": "Disabled",
                "auto": "Disabled (upstream default)",
                "unknown": "Unknown",
            }
            configuration_capabilities = (
                installed_capabilities if installed else available_capabilities
            )
            supported_modes = (
                ["automatic", "normal"]
                if configuration_capabilities.supports_fsr4
                else []
            )
            if configuration_capabilities.supports_force_int8:
                supported_modes.append("force_int8")
            if configuration_capabilities.fsr4_update:
                supported_modes.append("disabled")
            if recommendation.capability == "unsupported" and supported_modes:
                supported_modes = ["automatic", "disabled"]
            result.update(
                {
                    "onlineState": online_state,
                    "availableVersion": available_version,
                    "releaseUrl": release.html_url if release is not None else "",
                    "releaseSource": release.source if release is not None else "",
                    "releaseStale": bool(release.stale) if release is not None else False,
                    "archiveReady": cached_archive is not None,
                    "cachedArchivePath": (
                        str(cached_archive.path) if cached_archive is not None else ""
                    ),
                    "cachedArchiveSha256": (
                        cached_archive.sha256 if cached_archive is not None else ""
                    ),
                    "onlineError": self._app._optiscaler_online_errors.get(app_id, ""),
                    "availableChannel": release.channel if release is not None else channel,
                    "availableFidelityFxUpscalerVersion": (
                        release.fidelityfx_upscaler_version
                        if release is not None else ""
                    ),
                    "availableIniCapabilities": available_capabilities.to_dict(),
                    "supportedFsr4Modes": supported_modes,
                    "recommendation": recommendation.to_dict(),
                    "watermarkRequestedLabel": (
                        "Enabled"
                        if bool(result.get("requestedFsr4Watermark", False))
                        else "Disabled"
                    ),
                    "watermarkEffectiveIniLabel": effective_watermark_labels.get(
                        watermark_state, "Unknown"
                    ),
                    "runtimeOverlayStatus": "unknown",
                    "runtimeOverlayLabel": "Unknown until the game is observed",
                    "protonVersion": "Unknown",
                    "gamescopeEnabled": bool(optimization_profile.gamescope_enabled),
                    "gameModeEnabled": bool(optimization_profile.gamemode_enabled),
                    **detected,
                }
            )
            try:
                patcher_release = self._app._optipatcher_release_client.latest_release()
                result["optipatcher"] = self._app._optipatcher_service.status(
                    game, available=patcher_release
                )
                result["optipatcher"]["releaseUrl"] = patcher_release.html_url
            except OptiPatcherOnlineError as error:
                result.setdefault("optipatcher", {})["onlineError"] = str(error)
            return result
        except Exception as error:
            logger.exception("Could not inspect OptiScaler for %s", game.id)
            return {
                "success": False,
                "error": self._exception_text(
                    error, "Failed to refresh OptiScaler status"
                ),
            }

    def installOptiPatcher(
        self, game_id: str, force_refresh: bool = False
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            release = self._app._optipatcher_release_client.latest_release(
                force_refresh=bool(force_refresh)
            )
            asset, _digest = self._app._optipatcher_release_client.ensure_asset(release)
            status = self._app._optipatcher_service.install(game, release, asset)
            status.update({"success": True, "availableVersion": release.version})
            self._invalidate_status(game_id)
            return status
        except Exception as error:
            logger.warning("OptiPatcher installation failed for %s: %s", game_id, error)
            return {"success": False, "error": str(error)}

    def removeOptiPatcher(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            result = self._app._optipatcher_service.remove(game)
            result["success"] = True
            self._invalidate_status(game_id)
            return result
        except Exception as error:
            logger.warning("OptiPatcher removal failed for %s: %s", game_id, error)
            return {"success": False, "error": str(error)}

    def _cached_optiscaler_release(
        self, channel: str = "stable"
    ) -> OptiScalerRelease | None:
        loader = getattr(self._app._optiscaler_release_client, "cached_release", None)
        if not callable(loader):
            return None
        try:
            try:
                return loader(channel=channel)
            except TypeError:
                if channel != "stable":
                    return None
                return loader()
        except OptiScalerOnlineError as error:
            logger.warning("Could not read cached OptiScaler metadata: %s", error)
            return None

    def _cached_optiscaler_archive(
        self, release: OptiScalerRelease
    ) -> CachedOptiScalerArchive | None:
        loader = getattr(self._app._optiscaler_release_client, "cached_archive", None)
        if not callable(loader):
            return None
        try:
            return loader(release)
        except OptiScalerOnlineError as error:
            logger.warning("Could not read cached OptiScaler archive: %s", error)
            return None

    def rememberOptiScalerExecutable(
        self, game_id: str, executable_value: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        value = str(executable_value or "").strip()
        url = QUrl(value)
        if url.scheme():
            if not url.isLocalFile():
                return {"success": False, "error": "only local executables are supported"}
            value = url.toLocalFile()
        try:
            profile = self._app._optiscaler_service.remember_executable(game, value)
            self._app._optiscaler_service.executable_resolver.invalidate(game)
            self._invalidate_status(profile.app_id)
            self._app.optiScalerChanged.emit(profile.app_id)
            return self._profile_update_result(profile)
        except Exception as error:
            logger.warning("Could not save OptiScaler executable for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def setOptiScalerChannel(
        self, game_id: str, channel: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            profile = self._app._optiscaler_service.set_channel(game, channel)
            self._invalidate_status(profile.app_id)
            self._app.optiScalerChanged.emit(profile.app_id)
            return self._profile_update_result(profile)
        except Exception as error:
            return {"success": False, "error": str(error)}

    def setOptiScalerBackend(
        self, game_id: str, backend: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            profile = self._app._optiscaler_service.set_backend(game, backend)
            self._invalidate_status(profile.app_id)
            self._app.optiScalerChanged.emit(profile.app_id)
            return self._profile_update_result(profile)
        except Exception as error:
            logger.warning("Could not select OptiScaler backend for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def configureOptiScalerUpscaling(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            agility_value = values.get("fsrAgilitySdkUpgrade", False)
            watermark_value = values.get("fsr4Watermark", False)
            if not isinstance(agility_value, bool) or not isinstance(
                watermark_value, bool
            ):
                raise OptiScalerError(
                    "OptiScaler switch values must be booleans"
                )
            app_id = self._app._optiscaler_service.game_key(game)
            capabilities = self._app._optiscaler_service.ini_capabilities(game)
            requested_mode = str(values.get("fsr4Mode") or "automatic").casefold()
            context = self._detected_game_context(app_id)
            recommendation = recommend_fsr4(
                str(context["gpu"]), str(context["graphicsApi"]), capabilities
            )
            if (
                requested_mode in {"normal", "force_int8"}
                and recommendation.capability == "unsupported"
            ):
                raise OptiScalerError(recommendation.reason)
            effective_mode = requested_mode
            automatic_reason = ""
            if requested_mode == "automatic":
                candidate = recommendation.recommended_mode
                effective_mode = (
                    candidate
                    if candidate in {"normal", "force_int8"}
                    else "disabled"
                    if capabilities.fsr4_update
                    else "automatic"
                )
                automatic_reason = recommendation.reason
            profile = self._app._optiscaler_service.configure_upscaling(
                game,
                fsr4_mode=requested_mode,
                effective_fsr4_mode=effective_mode,
                automatic_reason=automatic_reason,
                fsr_agility_sdk_upgrade=agility_value,
                fsr4_watermark=watermark_value,
                dx11_upscaler=str(values.get("dx11Upscaler") or "auto"),
                dx12_upscaler=str(values.get("dx12Upscaler") or "auto"),
                vulkan_upscaler=str(values.get("vulkanUpscaler") or "auto"),
            )
            self._invalidate_status(profile.app_id)
            self._app.optiScalerChanged.emit(profile.app_id)
            return self._profile_update_result(profile)
        except Exception as error:
            logger.warning(
                "Could not configure OptiScaler upscaling for %s: %s",
                game.id,
                error,
            )
            return {"success": False, "error": str(error)}

    def refreshOptiScalerRelease(self, game_id: str, force_refresh: bool) -> bool:
        """Fetch and validate the official release without blocking the GUI."""

        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        try:
            app_id = self._app._optiscaler_service.game_key(game)
            channel = self._app._optiscaler_service.profile_repository.load(
                app_id
            ).channel
        except OptiScalerError:
            return False

        def operation(
            cancelled: Event, progress: Callable[[str, float], None]
        ) -> OptiScalerProfile:
            try:
                progress("Checking official release", 0.08)
                release = self._app._optiscaler_release_client.latest_release(
                    channel=channel,
                    force_refresh=bool(force_refresh),
                    allow_stale_cache=True,
                )
                if cancelled.is_set():
                    raise OptiScalerCancelled("OptiScaler operation was cancelled")
                progress("Downloading and validating release", 0.35)
                self._app._optiscaler_release_client.ensure_archive(release)
                if cancelled.is_set():
                    raise OptiScalerCancelled("OptiScaler operation was cancelled")
                self._app._optiscaler_online_errors.pop(app_id, None)
                progress("Release ready", 1.0)
                return self._app._optiscaler_service.profile_repository.load(app_id)
            except OptiScalerOnlineError as error:
                self._app._optiscaler_online_errors[app_id] = str(error)
                raise

        return self._app._start_optiscaler_operation(
            game,
            "Check release",
            operation,
        )

    def inspectOnlineOptiScaler(
        self,
        game_id: str,
        executable: str,
        injection_dll: str,
        allow_anticheat_risk: bool,
        fsr4_mode: str = "",
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        channel = self._app._optiscaler_service.profile_repository.load(
            self._app._optiscaler_service.game_key(game)
        ).channel
        release = self._cached_optiscaler_release(channel)
        archive = (
            self._app._cached_optiscaler_archive(release)
            if release is not None else None
        )
        if release is None or archive is None:
            return {
                "success": False,
                "error": "Check the official OptiScaler release before creating an installation plan",
            }
        try:
            result = self._app._optiscaler_service.plan(
                game,
                archive.path,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                requested_fsr4_mode=str(fsr4_mode or ""),
                allow_anticheat_risk=bool(allow_anticheat_risk),
                version_override=release.version,
            ).to_dict()
            result.update(
                {
                    "officialRelease": True,
                    "releaseUrl": release.html_url,
                    "archiveSha256": archive.sha256,
                    "archiveFromCache": archive.from_cache,
                }
            )
            return result
        except Exception as error:
            logger.warning("Online OptiScaler plan rejected for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def installOnlineOptiScaler(
        self,
        game_id: str,
        executable: str,
        injection_dll: str,
        operation_name: str,
        allow_replace_conflicts: bool,
        allow_anticheat_risk: bool,
        configuration: Mapping[str, Any] | None = None,
    ) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        channel = self._app._optiscaler_service.profile_repository.load(
            self._app._optiscaler_service.game_key(game)
        ).channel
        release = self._cached_optiscaler_release(channel)
        archive = (
            self._app._cached_optiscaler_archive(release)
            if release is not None else None
        )
        if release is None or archive is None:
            self._app._emit_toast(
                "Check the official OptiScaler release before installation",
                "warning",
            )
            return False
        operation = str(operation_name or "auto").strip().casefold()
        previous_profile = (
            self._app._optiscaler_service.profile_repository.load(
                self._app._optiscaler_service.game_key(game)
            )
        )
        desired = dict(configuration or {})

        def install_operation(
            cancelled: Event, progress: Callable[[str, float], None]
        ) -> OptiScalerProfile:
            installed_profile = self._app._optiscaler_service.install(
                game,
                archive.path,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                operation=operation,
                allow_replace_conflicts=bool(allow_replace_conflicts),
                allow_anticheat_risk=bool(allow_anticheat_risk),
                cancel_event=cancelled,
                progress=progress,
                expected_archive_sha256=archive.sha256,
                source_identity="official_optiscaler",
                channel=release.channel,
                fidelityfx_upscaler_version=(
                    release.fidelityfx_upscaler_version
                ),
                release_version=release.version,
                configuration=desired,
            )
            try:
                requested_mode = str(
                    desired.get("fsr4Mode", previous_profile.fsr4_mode)
                ).casefold()
                effective_mode = str(
                    desired.get(
                        "effectiveFsr4Mode",
                        previous_profile.effective_fsr4_mode,
                    )
                ).casefold()
                agility = desired.get(
                    "fsrAgilitySdkUpgrade",
                    previous_profile.fsr_agility_sdk_upgrade,
                )
                watermark = desired.get(
                    "fsr4Watermark", previous_profile.fsr4_watermark
                )
                if not isinstance(agility, bool) or not isinstance(watermark, bool):
                    raise OptiScalerError(
                        "OptiScaler switch values must be booleans"
                    )
                return self._app._optiscaler_service.configure_upscaling(
                    game,
                    fsr4_mode=requested_mode,
                    effective_fsr4_mode=effective_mode,
                    automatic_reason=str(
                        desired.get(
                            "automaticReason", previous_profile.automatic_reason
                        )
                    ),
                    fsr_agility_sdk_upgrade=agility,
                    fsr4_watermark=watermark,
                    dx11_upscaler=str(
                        desired.get("dx11Upscaler", previous_profile.dx11_upscaler)
                    ),
                    dx12_upscaler=str(
                        desired.get("dx12Upscaler", previous_profile.dx12_upscaler)
                    ),
                    vulkan_upscaler=str(
                        desired.get(
                            "vulkanUpscaler", previous_profile.vulkan_upscaler
                        )
                    ),
                )
            except OptiScalerError as error:
                logger.exception(
                    "Installed OptiScaler %s but could not reapply settings: %s",
                    release.version,
                    error,
                )
                try:
                    self._app._optiscaler_service.remove(game)
                except OptiScalerError as rollback_error:
                    raise OptiScalerError(
                        "OptiScaler configuration failed and rollback also failed: "
                        f"{error}; {rollback_error}"
                    ) from rollback_error
                raise OptiScalerError(
                    "OptiScaler configuration failed; the installation was rolled back: "
                    f"{error}"
                ) from error

        return self._app._start_optiscaler_operation(
            game,
            operation.capitalize(),
            install_operation,
        )

    def inspectOptiScalerArchive(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
        fsr4_mode: str = "",
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            archive = self._app._local_file_argument(archive_value)
            return self._app._optiscaler_service.plan(
                game,
                archive,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                requested_fsr4_mode=str(fsr4_mode or ""),
            ).to_dict()
        except Exception as error:
            logger.warning("OptiScaler plan rejected for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def _start_optiscaler_operation(
        self,
        game: Game,
        action: str,
        operation: Callable[
            [Event, Callable[[str, float], None]], Any
        ],
    ) -> bool:
        if any(
            stored_game_id == game.id and not future.done()
            for future, _cancel, stored_game_id in self._app._optiscaler_jobs.values()
        ):
            self._app._emit_toast("An OptiScaler task for this game is already active", "warning")
            return False
        task_id = f"optiscaler-{action.casefold()}-{uuid4().hex}"
        cancel_event = Event()
        timestamp = datetime.now(UTC).isoformat()
        self._app._operational_tasks[task_id] = self._app._operational_task(
            task_id=task_id,
            title=f"OptiScaler: {action} - {game.name}",
            operation="OptiScaler",
            status="queued",
            progress=0.0,
            game_id=game.id,
            game_name=game.name,
            created_at=timestamp,
        )
        self._app._operational_tasks[task_id]["cancellable"] = True
        self._app._operational_tasks[task_id]["stage"] = "Queued"

        def report(stage: str, value: float) -> None:
            self._app._optiscaler_events.put((task_id, str(stage), float(value)))

        future = self._app._optiscaler_executor.submit(operation, cancel_event, report)
        self._app._optiscaler_jobs[task_id] = (future, cancel_event, game.id)
        self._app._reload_tasks()
        self._app._emit_toast(f"OptiScaler {action.casefold()} started", "info")
        return True

    def installOptiScaler(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
        allow_replace_conflicts: bool,
        configuration: Mapping[str, Any] | None = None,
    ) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        try:
            archive = self._app._local_file_argument(archive_value)
        except ValueError as error:
            self._app._emit_toast(str(error), "error")
            return False
        return self._app._start_optiscaler_operation(
            game,
            "Install",
            lambda cancelled, progress: self._app._optiscaler_service.install(
                game,
                archive,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                allow_replace_conflicts=bool(allow_replace_conflicts),
                cancel_event=cancelled,
                progress=progress,
                configuration=configuration,
            ),
        )

    def removeOptiScaler(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        return bool(
            game is not None
            and self._app._start_optiscaler_operation(
                game,
                "Remove",
                lambda cancelled, progress: self._app._optiscaler_service.remove(
                    game, cancel_event=cancelled, progress=progress
                ),
            )
        )

    def restoreOptiScalerFiles(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        return bool(
            game is not None
            and self._app._start_optiscaler_operation(
                game,
                "Restore",
                lambda cancelled, progress: self._app._optiscaler_service.restore(
                    game, cancel_event=cancelled, progress=progress
                ),
            )
        )

    def verifyOptiScaler(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        return self._app._start_optiscaler_operation(
            game,
            "Verify",
            lambda _cancelled, _progress: self._app._optiscaler_service.verify(game),
        )

    def openOptiScalerDirectory(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        profile = self._app._optiscaler_service.profile_repository.load(
            self._app._optiscaler_service.game_key(game)
        )
        directory = Path(profile.install_directory)
        return bool(
            directory.is_dir()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        )

    def openOptiScalerManifest(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        profile = self._app._optiscaler_service.profile_repository.load(
            self._app._optiscaler_service.game_key(game)
        )
        manifest = self._app._optiscaler_service.manifest_path(
            profile.app_id, profile.manifest_id
        )
        return bool(
            manifest.is_file()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(manifest)))
        )

    def _poll_optiscaler_jobs(self) -> None:
        while True:
            try:
                task_id, stage, progress = self._app._optiscaler_events.get_nowait()
            except Empty:
                break
            task = self._app._operational_tasks.get(task_id)
            if task is None:
                continue
            task["status"] = "running"
            task["stage"] = stage
            task["progress"] = min(1.0, max(0.0, progress))
            task["progressPercent"] = task["progress"] * 100.0
            task["updatedAt"] = datetime.now(UTC).isoformat()

        for task_id, (future, cancelled, game_id) in tuple(
            self._app._optiscaler_jobs.items()
        ):
            if not future.done():
                continue
            self._app._optiscaler_jobs.pop(task_id, None)
            task = self._app._operational_tasks.get(task_id)
            if task is None:
                continue
            status = "completed"
            error_text = ""
            app_id = self._status_app_ids.get(game_id, "")
            try:
                profile = future.result()
                task["result"] = profile.to_dict()
                app_id = profile.app_id
                self._operation_errors.pop(game_id, None)
                self._operation_conflicts.pop(game_id, None)
            except OptiScalerCancelled as error:
                status = "cancelled"
                error_text = self._exception_text(error, "Operation cancelled")
            except Exception as error:
                status = "cancelled" if cancelled.is_set() else "failed"
                error_text = self._exception_text(
                    error,
                    f"OptiScaler {str(task.get('title', 'operation')).split(':', 1)[-1].strip()} failed",
                )
                logger.exception("OptiScaler task %s failed", task_id)
                self._operation_errors[game_id] = error_text
                if isinstance(error, OptiScalerConflictError):
                    match = re.search(r":\s*([^:]+)$", str(error))
                    self._operation_conflicts[game_id] = {
                        "kind": "managed_file_conflict",
                        "path": match.group(1).strip() if match else "",
                        "message": str(error),
                    }
                else:
                    self._operation_conflicts.pop(game_id, None)
            if not app_id:
                resolved_game = self._app._resolve_game(game_id, show_error=False)
                if resolved_game is not None:
                    try:
                        app_id = self._app._optiscaler_service.game_key(
                            resolved_game
                        )
                    except OptiScalerError:
                        app_id = ""
            self._invalidate_status(app_id)
            self.requestOptiScalerStatus(game_id, True)
            if app_id:
                self._app.optiScalerChanged.emit(app_id)
            task["status"] = status
            task["stage"] = (
                "Completed" if status == "completed"
                else "Cancelled" if status == "cancelled"
                else "Failed"
            )
            task["progress"] = 1.0
            task["progressPercent"] = 100.0
            task["error"] = error_text
            task["cancellable"] = False
            task["updatedAt"] = datetime.now(UTC).isoformat()
            self._app.taskFinished.emit(task_id, status)
            self._app._emit_toast(
                str(getattr(profile, "summary", "OptiScaler operation completed"))
                if status == "completed"
                else "OptiScaler operation cancelled"
                if status == "cancelled"
                else f"OptiScaler operation failed: {error_text}",
                "success" if status == "completed" else "warning"
                if status == "cancelled" else "error",
            )
