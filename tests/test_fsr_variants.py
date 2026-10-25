"""FSR 4 Lite / Cyboman variants on isolated game-folder fixtures."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services import (
    GameExecutableResolver,
    OptiScalerError,
    OptiScalerProfileRepository,
    OptiScalerService,
    inspect_optiscaler_ini,
    inspect_optiscaler_ini_state,
)
from game_optimization_linux.services import fsr_variants as fv
from game_optimization_linux.services.optiscaler_fsr4 import (
    managed_ini_updates,
    managed_option_updates,
)

OFFICIAL = b"official fsr 4.1.1.2740"
EXACT = b"cyboman exact"
LOSSY2 = b"cyboman lossy2"
VK = b"amdvkffx64 4.1.2"
XC = b"amdxcffx64 4.1.2"

V10_INI = """\
[Upscalers]
; fsr22 (native DX11), ffx_12 (FSR 2.3; 3.1; 4.x), dlss - Default (auto) is fsr22
Dx11Upscaler=auto
; xess, ffx (FSR 2.3; 3.1; 4.x), dlss
Dx12Upscaler=auto
; fsr21 (native VK), ffx (native FSR 2.3; 3.1), ffx_12 (FSR 2.3; 3.1; 4.x), dlss
VulkanUpscaler=auto
[FSR]
; 0 = No override | 1 = FP8 | 2 = INT8 | 3 = INT8-2 | 4 = INT8 Lite
Fsr4GpuSupport=auto
Fsr4Preset=auto
; 0 = FP8 | 1 = INT8 | 2 = INT8 RDNA2 | 3 = INT8 Lite
Fsr4Model=auto
Fsr4EnableWatermark=auto
FsrAgilitySDKUpgrade=auto
; user comment kept
CustomUnknownKey=keep-me
[Sharpness]
Shader=auto
OverrideSharpness=auto
Sharpness=auto
[Menu]
ShortcutKey=auto
[Log]
LogToFile=auto
LogLevel=auto
"""
STABLE_INI = """\
[Upscalers]
Dx12Upscaler=auto
[FSR]
Fsr4Update=auto
Fsr4ForceEnableInt8=auto
Fsr4Preset=auto
Fsr4EnableWatermark=auto
"""


def _h(data: bytes) -> str:
    return sha256(data).hexdigest()


def _game(root: Path) -> Game:
    exe = root / "Bin" / "Game.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"exe")
    return Game(
        id="steam-224760", steam_app_id="224760", name="Fixture",
        launcher=Launcher.STEAM, install_path=root, logical_size_gb=0.01,
        physical_size_gb=0.01, filesystem=FilesystemType.EXT4,
        compression_available=False,
    )


def _release(path: Path, ini: str, *, v10: bool = True) -> Path:
    prefix = "OptiScaler/" if v10 else ""
    with ZipFile(path, "w") as archive:
        archive.writestr("OptiScaler.dll", b"proxy")
        archive.writestr("OptiScaler.ini", ini)
        archive.writestr(prefix + fv.UPSCALER_DLL, OFFICIAL)
    return path


def _artifact_zip(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class _Response:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self._stream = BytesIO(data)
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class _Opener:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []
        self.fail = False

    def __call__(self, request, timeout: float = 0):  # noqa: ANN001
        url = request.full_url
        self.calls.append(url)
        if self.fail:
            raise OSError("network down")
        return _Response(self.payloads[url])



@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cyb = _artifact_zip({
        "exact/" + fv.UPSCALER_DLL: EXACT,
        "lossy2/" + fv.UPSCALER_DLL: LOSSY2,
        "lossy2/WARNING.txt": b"changes the picture",
    })
    decky = _artifact_zip({
        "plugin/bin/OptiScaler/amdvkffx64.dll": VK,
        "plugin/bin/OptiScaler/amdxcffx64.dll": XC,
    })
    cyb_archive = fv.PinnedArchive("cyb", "https://example.invalid/cyb.zip", len(cyb), _h(cyb))
    decky_archive = fv.PinnedArchive("decky", "https://example.invalid/decky.zip", len(decky), _h(decky))
    base = fv.FSR_ARTIFACTS
    artifacts = {
        fv.DRIVER_ARTIFACT_KEY: fv.FsrArtifact(
            **{**_fields(base[fv.DRIVER_ARTIFACT_KEY]), "archive": decky_archive,
               "files": (fv.PinnedFile("amdvkffx64.dll", _h(VK), len(VK)),
                         fv.PinnedFile("amdxcffx64.dll", _h(XC), len(XC)))}),
        "cyboman_exact": fv.FsrArtifact(
            **{**_fields(base["cyboman_exact"]), "archive": cyb_archive,
               "files": (fv.PinnedFile(fv.UPSCALER_DLL, _h(EXACT), len(EXACT)),)}),
        "cyboman_lossy2": fv.FsrArtifact(
            **{**_fields(base["cyboman_lossy2"]), "archive": cyb_archive,
               "files": (fv.PinnedFile(fv.UPSCALER_DLL, _h(LOSSY2), len(LOSSY2)),)}),
    }
    monkeypatch.setattr(fv, "FSR_ARTIFACTS", artifacts)
    monkeypatch.setattr(fv, "OFFICIAL_UPSCALER_SHA256", _h(OFFICIAL))
    opener = _Opener({cyb_archive.url: cyb, decky_archive.url: decky})
    root = tmp_path / "game"
    game = _game(root)
    service = OptiScalerService(
        profile_repository=OptiScalerProfileRepository(tmp_path / "config"),
        data_root=tmp_path / "data",
        executable_resolver=GameExecutableResolver(),
        process_detector=lambda _path: (),
    )
    variants = fv.FsrVariantService(
        service, fv.FsrArtifactClient(tmp_path / "cache", opener=opener)
    )
    return service, variants, game, root / "Bin", opener, tmp_path


def _fields(artifact: fv.FsrArtifact) -> dict:
    return {name: getattr(artifact, name) for name in artifact.__dataclass_fields__}


def _install(env, ini: str = V10_INI, *, v10: bool = True):
    service, _variants, game, _bin, _opener, tmp = env
    archive = _release(tmp / f"OptiScaler_{'v10' if v10 else '094'}.zip", ini, v10=v10)
    return service.install(game, archive, executable="Bin/Game.exe")


def _configure(service, game, mode: str, **extra):
    return service.configure_upscaling(
        game, fsr4_mode=mode, effective_fsr4_mode=mode,
        fsr_agility_sdk_upgrade=False, fsr4_watermark=True,
        dx11_upscaler="auto", dx12_upscaler="ffx", vulkan_upscaler="auto", **extra,
    )


def test_v10_schema_maps_lite_int8_and_full_model() -> None:
    caps = inspect_optiscaler_ini(V10_INI)
    assert caps.force_int8_style == "gpu_support"
    assert caps.supports_lite and caps.fsr4_preset and caps.sharpness and caps.log
    common = dict(agility_sdk_upgrade=False, watermark=False, dx11_upscaler="auto",
                  dx12_upscaler="ffx", vulkan_upscaler="auto", capabilities=caps)
    lite = managed_ini_updates(fsr4_mode="lite", **common)
    assert lite[("FSR", "Fsr4GpuSupport")] == "4"
    assert lite[("FSR", "Fsr4Model")] == "3"
    int8 = managed_ini_updates(fsr4_mode="force_int8", **common)
    assert int8[("FSR", "Fsr4GpuSupport")] == "2" and int8[("FSR", "Fsr4Model")] == "auto"
    assert ("FSR", "Fsr4Update") not in int8 and ("FSR", "Fsr4ForceEnableInt8") not in int8


def test_lite_is_rejected_on_stable_094_schema() -> None:
    caps = inspect_optiscaler_ini(STABLE_INI)
    assert caps.force_int8_style == "boolean" and not caps.supports_lite
    with pytest.raises(ValueError, match="OptiScaler v10"):
        managed_ini_updates(
            fsr4_mode="lite", agility_sdk_upgrade=False, watermark=False,
            dx11_upscaler="auto", dx12_upscaler="auto", vulkan_upscaler="auto",
            capabilities=caps,
        )


def test_additional_options_require_keys_and_support_default() -> None:
    caps = inspect_optiscaler_ini(STABLE_INI)
    assert managed_option_updates(
        fsr4_preset="3", sharpness="auto", log_mode="auto",
        overlay_shortcut="auto", capabilities=caps,
    ) == {("FSR", "Fsr4Preset"): "3"}
    with pytest.raises(ValueError, match="Sharpness"):
        managed_option_updates(
            fsr4_preset="auto", sharpness="0.4", log_mode="auto",
            overlay_shortcut="auto", capabilities=caps,
        )
    with pytest.raises(ValueError):
        managed_option_updates(
            fsr4_preset="9", sharpness="auto", log_mode="auto",
            overlay_shortcut="auto", capabilities=inspect_optiscaler_ini(V10_INI),
        )


def test_options_persist_reach_ini_and_preserve_unknown_entries(env) -> None:
    service, _v, game, bin_dir, _o, _t = env
    _install(env)
    _configure(service, game, "force_int8", fsr4_preset="2", sharpness="0.35",
               log_mode="debug", overlay_shortcut="0x24")
    text = (bin_dir / "OptiScaler.ini").read_text()
    for line in ("Fsr4GpuSupport=2", "Fsr4Preset=2", "OverrideSharpness=true",
                 "Sharpness=0.35", "LogToFile=true", "LogLevel=1",
                 "ShortcutKey=0x24", "CustomUnknownKey=keep-me", "; user comment kept"):
        assert line in text
    profile = service.profile_repository.load("224760")
    assert (profile.fsr4_preset, profile.sharpness, profile.log_mode,
            profile.overlay_shortcut) == ("2", "0.35", "debug", "0x24")
    # A caller unaware of the new options keeps them (None = unchanged).
    _configure(service, game, "normal")
    reloaded = OptiScalerProfileRepository(service.profile_repository.root).load("224760")
    assert reloaded.sharpness == "0.35"
    state = inspect_optiscaler_ini_state((bin_dir / "OptiScaler.ini").read_text())
    assert state.fsr4_mode == "normal" and state.sharpness == "0.35"
    # Back to upstream defaults.
    _configure(service, game, "automatic", fsr4_preset="auto", sharpness="auto",
               log_mode="auto", overlay_shortcut="auto")
    text = (bin_dir / "OptiScaler.ini").read_text()
    assert "Fsr4GpuSupport=auto" in text and "OverrideSharpness=auto" in text
    assert "LogToFile=auto" in text and "ShortcutKey=auto" in text
    assert service.verify(game).state == "verified"


def test_runtime_log_detects_lite_and_fsr3_fallback(tmp_path: Path) -> None:
    log = tmp_path / "OptiScaler.log"
    log.write_text(
        "[info] amdvkffx64.dll loaded from game folder\n"
        "[info] Hooked the support check of ffxProvider_FSR4Lite_Int8 in amdvkffx64.dll\n"
        "[info] FfxApi Vk SR version: 4.1.2\n"
    )
    assert fv.inspect_runtime_log(log)["status"] == "log_indicates_fsr4_int8_lite"
    log.write_text("[info] GPU doesn't support FSR 4, skipping the driver upscaler\n")
    assert fv.inspect_runtime_log(log)["status"] == "verified_fsr3_fallback"
    assert fv.inspect_runtime_log(tmp_path / "missing.log")["logFound"] is False



def test_cyboman_switching_and_restore_keep_verification(env) -> None:
    service, variants, game, bin_dir, opener, _t = env
    _install(env)
    dll = bin_dir / "OptiScaler" / fv.UPSCALER_DLL
    variants.apply_variant(game, "cyboman_exact", gpu="AMD Radeon RX 6650 XT")
    assert dll.read_bytes() == EXACT
    assert service.verify(game).state == "verified"
    variants.apply_variant(game, "cyboman_lossy2", gpu="AMD Radeon RX 6650 XT")
    assert dll.read_bytes() == LOSSY2
    assert len(opener.calls) == 1  # archive cached and reused
    status = variants.status(game)
    assert status["variant"] == "cyboman_lossy2" and not status["drift"]
    assert service.profile_repository.load("224760").fsr_variant == "cyboman_lossy2"
    variants.restore_variant(game)
    assert dll.read_bytes() == OFFICIAL
    assert service.verify(game).state == "verified"
    assert service.profile_repository.load("224760").fsr_variant == "official"
    service.remove(game)
    assert not [p for p in (bin_dir / "OptiScaler").rglob("*") if p.is_file()]


def test_cyboman_blocked_on_rdna4_and_foreign_dll(env) -> None:
    _service, variants, game, bin_dir, _o, _t = env
    _install(env)
    with pytest.raises(OptiScalerError, match="RX 9000"):
        variants.apply_variant(game, "cyboman_exact", gpu="AMD Radeon RX 9070 XT")
    (bin_dir / "OptiScaler" / fv.UPSCALER_DLL).write_bytes(b"user modified")
    with pytest.raises(OptiScalerError, match="changed outside"):
        variants.apply_variant(game, "cyboman_exact", gpu="RX 6650 XT")
    assert (bin_dir / "OptiScaler" / fv.UPSCALER_DLL).read_bytes() == b"user modified"


def test_failed_download_and_bad_checksum_change_nothing(env) -> None:
    service, variants, game, bin_dir, opener, _t = env
    _install(env)
    manifest = next(service.data_root.rglob("manifests/*.json"))
    before = manifest.read_bytes()
    opener.fail = True
    with pytest.raises(OptiScalerError, match="download"):
        variants.apply_variant(game, "cyboman_exact", gpu="RX 6650 XT")
    opener.fail = False
    url = fv.FSR_ARTIFACTS["cyboman_exact"].archive.url
    opener.payloads[url] = opener.payloads[url][:-1] + b"X"
    with pytest.raises(OptiScalerError, match="checksum"):
        variants.apply_variant(game, "cyboman_exact", gpu="RX 6650 XT")
    assert (bin_dir / "OptiScaler" / fv.UPSCALER_DLL).read_bytes() == OFFICIAL
    assert manifest.read_bytes() == before
    assert service.verify(game).state == "verified"


def test_write_failure_rolls_back_files_and_manifest(env, monkeypatch) -> None:
    service, variants, game, bin_dir, _o, _t = env
    _install(env)
    original_copy = service._copy_atomic

    def flaky(source, target):  # noqa: ANN001
        if Path(target).name == "amdxcffx64.dll":
            raise OSError("disk full")
        return original_copy(source, target)

    monkeypatch.setattr(service, "_copy_atomic", flaky)
    with pytest.raises(OSError):
        variants.install_driver(game)
    monkeypatch.setattr(service, "_copy_atomic", original_copy)
    assert not (bin_dir / "OptiScaler" / "amdvkffx64.dll").exists()
    assert variants.status(game)["driverInstalled"] is False
    assert service.verify(game).state == "verified"


def test_lite_driver_install_configure_and_remove(env) -> None:
    service, variants, game, bin_dir, _o, _t = env
    _install(env)
    variants.install_driver(game)
    assert (bin_dir / "OptiScaler" / "amdvkffx64.dll").read_bytes() == VK
    _configure(service, game, "lite")
    text = (bin_dir / "OptiScaler.ini").read_text()
    assert "Fsr4GpuSupport=4" in text and "Fsr4Model=3" in text
    assert service.status(game)["effectiveFsr4Mode"] == "lite"
    assert service.verify(game).state == "verified"
    with pytest.raises(OptiScalerError, match="4.1.1"):
        variants.apply_variant(game, "cyboman_exact", gpu="RX 6650 XT")
    _configure(service, game, "force_int8")
    variants.remove_driver(game)
    assert not (bin_dir / "OptiScaler" / "amdvkffx64.dll").exists()
    service.remove(game)
    assert not [p for p in (bin_dir / "OptiScaler").rglob("*") if p.is_file()]


def test_lite_driver_requires_v10_layout(env) -> None:
    _service, variants, game, _bin, _o, _t = env
    _install(env, STABLE_INI, v10=False)
    with pytest.raises(OptiScalerError, match="v10"):
        variants.install_driver(game)
