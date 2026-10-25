"""Small, depth-independent effect catalogue for pinned post-processing engines."""
from __future__ import annotations

from typing import Any

# key, label, minimum, maximum, default. Parameters are the upstream spellings.
def parameter(key: str, label: str, low: float, high: float, default: float) -> dict[str, Any]:
    return dict(key=key, label=label, minimum=low, maximum=high, default=default,
                integer=key in {"MaxSearchSteps", "MaxSearchStepsDiagonal", "CornerRounding"})


EFFECTS = {
    "cas": dict(label="CAS", backends=["vkbasalt"], parameters=[
        parameter("casSharpness", "Sharpness", 0, 1, 0.3)]),
    "dls": dict(label="Denoised Luma Sharpening", backends=["vkbasalt"], parameters=[
        parameter("dlsSharpness", "Sharpness", 0, 1, 0.3),
        parameter("dlsDenoise", "Denoise", 0, 1, 0.17)]),
    "fxaa": dict(label="FXAA", backends=["vkbasalt", "reshade"], shader="FXAA.fx", technique="FXAA",
                 parameters=[parameter("Subpix", "Subpixel filtering", 0, 1, 0.25),
                             parameter("EdgeThreshold", "Edge threshold", 0.063, 0.333, 0.125),
                             parameter("EdgeThresholdMin", "Darkness threshold", 0, 0.0833, 0.0312)]),
    "smaa": dict(label="SMAA (colour edges)", backends=["vkbasalt", "reshade"], shader="SMAA.fx", technique="SMAA",
                 parameters=[parameter("EdgeDetectionThreshold", "Edge threshold", 0.05, 0.2, 0.1),
                             parameter("MaxSearchSteps", "Search steps", 0, 112, 32),
                             parameter("MaxSearchStepsDiagonal", "Diagonal steps", 0, 20, 16),
                             parameter("CornerRounding", "Corner rounding", 0, 100, 25)]),
    "lut": dict(label="LUT", backends=["vkbasalt"], parameters=[]),
    "vibrance": dict(label="Vibrance", backends=["vkbasalt", "reshade"], shader="Vibrance.fx", technique="Vibrance",
                     parameters=[parameter("Vibrance", "Vibrance", -1, 1, 0)]),
    "tonemap": dict(label="Tonemap", backends=["vkbasalt", "reshade"], shader="Tonemap.fx", technique="Tonemap",
                    parameters=[parameter("Gamma", "Gamma", 0.1, 2, 1),
                                parameter("Exposure", "Exposure", -1, 1, 0),
                                parameter("Saturation", "Saturation", -1, 1, 0),
                                parameter("Bleach", "Bleach", 0, 1, 0)]),
    "curves": dict(label="Curves", backends=["vkbasalt", "reshade"], shader="Curves.fx", technique="Curves",
                   parameters=[parameter("Contrast", "Contrast", -1, 1, 0)]),
    "lumasharpen": dict(label="LumaSharpen", backends=["vkbasalt", "reshade"], shader="LumaSharpen.fx", technique="LumaSharpen",
                        parameters=[parameter("sharp_strength", "Sharpness", 0.1, 3, 0.35),
                                    parameter("sharp_clamp", "Sharpening limit", 0, 1, 0.035)]),
}
VK_PARAMETER_NAMES = {
    "Subpix": "fxaaQualitySubpix", "EdgeThreshold": "fxaaQualityEdgeThreshold",
    "EdgeThresholdMin": "fxaaQualityEdgeThresholdMin", "EdgeDetectionThreshold": "smaaThreshold",
    "MaxSearchSteps": "smaaMaxSearchSteps", "MaxSearchStepsDiagonal": "smaaMaxSearchStepsDiag",
    "CornerRounding": "smaaCornerRounding",
}
PRESETS = {
    "neutral": dict(effects=[], parameters={}),
    "colour": dict(effects=["vibrance", "curves"], parameters={"Vibrance": 0.12, "Contrast": 0.1}),
    "clarity": dict(effects=["fxaa", "lumasharpen"], parameters={"sharp_strength": 0.35}),
}
TOGGLE_KEYS = {"Home": 36, "End": 35, "Insert": 45, "F10": 121, "F11": 122}
