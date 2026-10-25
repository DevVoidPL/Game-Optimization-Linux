"""Pinned DLSS Enabler v0.9.4 files (verified 22.10.2026).

DLSS Enabler v0.9.4 (artur-graniszewski/DLSS-Enabler, tag commit
``c67224e4``) installs its main module as ``version.dll`` and bundles
OptiScaler **0.9.4** as ``dlss-enabler-upscaler.dll`` + ``nvngx.ini``.  The
AMD/Intel component adds ``dxgi.dll``, ``nvapi64-proxy.dll``, ``dlss-finder.exe``;
the finder replaces the initial ``_nvngx.dll`` placeholder with the game's
native ``nvngx_dlss.dll``. The DXGI loader also needs the main module named
``dlss-enabler.dll`` (upstream's ``mainfiles/dlldxgi`` option).
Hashes were taken from the repository files and from a silent install of the
official setup under Wine (``dlss-enabler-upscaler.dll`` == OptiScaler.dll
of the official 0.9.4 release).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .fsr_variants import FsrArtifact, PinnedArchive, PinnedFile

DLSS_ENABLER_VERSION = "0.9.4"
# The release/installer version, the main module's PE version and the bundled
# OptiScaler version are three different identities in this package.
DLSS_ENABLER_MODULE_VERSION = "3.00.000.0"
DLSS_ENABLER_OPTISCALER_VERSION = "0.9.4"
DLSS_ENABLER_COMMIT = "c67224e4ecb8020497b05de26b550e44d815eb59"
DLSS_ENABLER_SOURCE_URL = (
    "https://github.com/artur-graniszewski/DLSS-Enabler/releases/tag/v0.9.4"
)
_RAW = (
    "https://raw.githubusercontent.com/artur-graniszewski/DLSS-Enabler/"
    + DLSS_ENABLER_COMMIT
)


@dataclass(frozen=True, slots=True)
class EnablerFile:
    """A file placed next to the game executable and its pinned source."""

    target: str
    source_key: str
    sha256: str
    size: int
    amd_intel_only: bool = False


def _raw(key: str, path: str, size: int, sha: str) -> PinnedArchive:
    url = f"{_RAW}/{path}".replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return PinnedArchive(key=key, url=url, size=size, sha256=sha)


RAW_FILES: dict[str, PinnedArchive] = {
    "enabler_main": _raw("enabler_main", "Dll version/dlss-enabler.asi", 943616,
                         "a478031d27de7feca2d11896083d0d6eaf00f26e4b53e5d6e711341e36b72784"),
    "dlssg_wrapper": _raw("dlssg_wrapper", "DLLSG mod/nvngx.dll", 189952,
                          "6bb7f36f7dc3fffd588f2c6d2341c13f2a690ade0a7ef6c74ccdb2387cd5952e"),
    "dlssg_fsr3": _raw("dlssg_fsr3", "DLLSG mod/dlssg_to_fsr3_amd_is_better.dll", 3038208,
                       "806020c0444f7841a3a9f661295c16fc482e981e0666d0b9624bf6ccd19c4b5e"),
    "nv_dxgi": _raw("nv_dxgi", "NVIDIA Environment/dxgi.dll", 73728,
                    "9ae73670c5dbb80f40d98176cb4426992f0cb0b9d0143b1d8083838d03b7d6fc"),
    "nv_nvapi": _raw("nv_nvapi", "NVIDIA Environment/nvapi64-proxy.dll", 1310734,
                     "6e3bc9bc4efaaa3f18df1e6791e37dd5ef57baff5481c4f85ef09e193b09f738"),
    "dlss_finder": _raw("dlss_finder", "NVIDIA Environment/dlss-finder.bin", 83456,
                         "afacf14cf7d168aeaf12795ae71194c55f656032e65cc1706db819e597e4c6b4"),
    "license": _raw("license", "LICENSE", 1075,
                    "2939dfade42e0a7aaba2384a458b2d176abf3d563377711eef4f4c6077931505"),
    "license_enabler": _raw("license_enabler", "License (DLSS enabler).txt", 5118,
                            "05adc7aac12b028cb4d2db56f5eab82d2f12ae863f1ce6e5ffe1482b73d07603"),
    "license_dlssg": _raw("license_dlssg", "DLLSG mod/LICENSE.txt", 6800,
                          "e6f1d12e0233beea7ac9bcbc312e2ae04b0f0d0bece16b1c0460ca1bccd26422"),
}

UPSCALER_ARTIFACT = FsrArtifact(
    key="dlss-enabler-optiscaler-0.9.4",
    label="OptiScaler 0.9.4 (DLSS Enabler upscaler)",
    component="dlss_enabler",
    archive=PinnedArchive(
        key="optiscaler-0.9.4",
        url=(
            "https://github.com/optiscaler/OptiScaler/releases/download/v0.9.4/"
            "Optiscaler_0.9.4-final.20260718._MM.7z"
        ),
        size=55016448,
        sha256="575cb4df866116093df75af607e37fd70e10f5163e0f23fd5c804142e80ef0ad",
    ),
    files=(
        PinnedFile("OptiScaler.dll", "fbfb6676b829dad7e020fb830586a16aa0ec6add78016db48ef12e2ae1803231", 25379632),
        PinnedFile("OptiScaler.ini", "6d7ed07e2e9df3d611b6f59047716bcbc0b67834296b8e4414816189b1233aa7", 47477),
        PinnedFile("libxess.dll", "251659dd84a3e84de67c886a4186e01f3eca49b00641906fe38bb6b807e5d5b7", 77795704),
        PinnedFile("amd_fidelityfx_dx12.dll", "e2d85aa05a9bd9ed8b38935fdf5199372cca6f74c12015143bb6f945ee1608aa", 26376),
        PinnedFile("amd_fidelityfx_vk.dll", "a1624cc4238fef046f30c4d80ce3f47be63fc5f5373f49e3ee9edb9960f54c78", 9332432),
    ),
    fsr_version="3.1",
    model="",
    precision="",
    source="OptiScaler v0.9.4 (official release)",
    source_url="https://github.com/optiscaler/OptiScaler/releases/tag/v0.9.4",
    notes="Bundled by DLSS Enabler v0.9.4 as dlss-enabler-upscaler.dll",
)


def build_enabler_files(
    raw: Mapping[str, PinnedArchive], upscaler: FsrArtifact
) -> tuple[EnablerFile, ...]:
    """File layout of the v0.9.4 installer ('full' + AMD/Intel component)."""

    up = {item.name: item for item in upscaler.files}

    def r(target: str, key: str, amd: bool = False) -> EnablerFile:
        return EnablerFile(target, f"raw:{key}", raw[key].sha256, raw[key].size, amd)

    def u(target: str, name: str) -> EnablerFile:
        return EnablerFile(target, f"up:{name}", up[name].sha256, up[name].size)

    return (
        r("version.dll", "enabler_main"),
        u("dlss-enabler-upscaler.dll", "OptiScaler.dll"),
        u("nvngx.ini", "OptiScaler.ini"),
        u("libxess.dll", "libxess.dll"),
        u("amd_fidelityfx_dx12.dll", "amd_fidelityfx_dx12.dll"),
        u("amd_fidelityfx_vk.dll", "amd_fidelityfx_vk.dll"),
        r("dlssg_to_fsr3_amd_is_better.dll", "dlssg_fsr3"),
        r("nvngx-wrapper.dll", "dlssg_wrapper"),
        r("dxgi.dll", "nv_dxgi", True),
        # NVIDIA Environment/dxgi.dll loads this exact name.  Some games
        # (including Jedi: Survivor) never load the local version.dll.
        r("dlss-enabler.dll", "enabler_main", True),
        r("dlss-finder.exe", "dlss_finder", True),
        r("nvapi64-proxy.dll", "nv_nvapi", True),
        r("licenses/LICENSE (DLSS Enabler).txt", "license"),
        r("licenses/License (DLSS enabler).txt", "license_enabler"),
        r("licenses/LICENSE (DLSSG to FSR3 mod).txt", "license_dlssg"),
    )


def wine_overrides(amd_intel: bool) -> str:
    """version.dll loads the main module; AMD/Intel also need the dxgi proxy."""

    return "dxgi=n,b" if amd_intel else "version=n,b"


def needs_amd_intel_support(gpu: str) -> bool:
    return "nvidia" not in str(gpu or "").casefold()
