"""In-memory freeze-frame helpers for Narrator subtitle-region selection."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from math import isfinite

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from game_optimization_linux.models.narrator import CaptureFrame, NormalizedRect


REGION_PREVIEW_MAX_WIDTH = 1920
REGION_PREVIEW_MAX_HEIGHT = 1080


@dataclass(frozen=True, slots=True)
class PreviewContentRect:
    x: float
    y: float
    width: float
    height: float
    scale: float


@dataclass(frozen=True, slots=True)
class EncodedRegionPreview:
    data_url: str
    source_width: int
    source_height: int
    preview_width: int
    preview_height: int

    def to_dict(self) -> dict[str, object]:
        return {
            "success": True,
            "state": "ready",
            "imageUrl": self.data_url,
            "sourceWidth": self.source_width,
            "sourceHeight": self.source_height,
            "previewWidth": self.preview_width,
            "previewHeight": self.preview_height,
        }


def fitted_preview_content(
    source_width: int,
    source_height: int,
    viewport_width: float,
    viewport_height: float,
) -> PreviewContentRect:
    """Return the aspect-fit image rectangle inside a preview viewport."""

    if source_width <= 0 or source_height <= 0:
        raise ValueError("The captured frame dimensions must be positive")
    values = (float(viewport_width), float(viewport_height))
    if not all(isfinite(value) and value > 0 for value in values):
        raise ValueError("The preview dimensions must be positive and finite")
    scale = min(values[0] / source_width, values[1] / source_height)
    width = source_width * scale
    height = source_height * scale
    return PreviewContentRect(
        x=(values[0] - width) / 2.0,
        y=(values[1] - height) / 2.0,
        width=width,
        height=height,
        scale=scale,
    )


def normalized_region_from_preview(
    *,
    source_width: int,
    source_height: int,
    viewport_width: float,
    viewport_height: float,
    selection_x: float,
    selection_y: float,
    selection_width: float,
    selection_height: float,
) -> NormalizedRect:
    """Map a preview-space rectangle to the captured frame's normalized ROI."""

    numbers = tuple(
        float(value)
        for value in (
            selection_x,
            selection_y,
            selection_width,
            selection_height,
        )
    )
    if not all(isfinite(value) for value in numbers):
        raise ValueError("The subtitle selection must contain finite coordinates")
    content = fitted_preview_content(
        source_width,
        source_height,
        viewport_width,
        viewport_height,
    )
    left = max(content.x, min(content.x + content.width, numbers[0]))
    top = max(content.y, min(content.y + content.height, numbers[1]))
    right = max(
        content.x,
        min(content.x + content.width, numbers[0] + numbers[2]),
    )
    bottom = max(
        content.y,
        min(content.y + content.height, numbers[1] + numbers[3]),
    )
    if right <= left or bottom <= top:
        raise ValueError("Select a non-empty area inside the captured game frame")
    source_left = (left - content.x) / content.scale
    source_top = (top - content.y) / content.scale
    source_right = (right - content.x) / content.scale
    source_bottom = (bottom - content.y) / content.scale
    if source_right - source_left < 1.0 or source_bottom - source_top < 1.0:
        raise ValueError("The subtitle selection is too small")
    normalized_x = round(max(0.0, min(1.0, source_left / source_width)), 9)
    normalized_y = round(max(0.0, min(1.0, source_top / source_height)), 9)
    normalized_width = min(
        round(
            max(0.0, min(1.0, (source_right - source_left) / source_width)),
            9,
        ),
        1.0 - normalized_x,
    )
    normalized_height = min(
        round(
            max(0.0, min(1.0, (source_bottom - source_top) / source_height)),
            9,
        ),
        1.0 - normalized_y,
    )
    return NormalizedRect(
        x=normalized_x,
        y=normalized_y,
        width=normalized_width,
        height=normalized_height,
    )


def preview_rect_from_normalized(
    region: NormalizedRect,
    *,
    source_width: int,
    source_height: int,
    viewport_width: float,
    viewport_height: float,
) -> tuple[float, float, float, float]:
    content = fitted_preview_content(
        source_width,
        source_height,
        viewport_width,
        viewport_height,
    )
    return (
        content.x + region.x * content.width,
        content.y + region.y * content.height,
        region.width * content.width,
        region.height * content.height,
    )


def encode_region_preview(frame: CaptureFrame) -> EncodedRegionPreview:
    """Encode a bounded in-memory PNG preview without persisting the frame."""

    formats = {
        # The GStreamer ScreenCast transport emits binary PPM/P6 pixels under
        # the canonical rgb888 name: three bytes per pixel in R, G, B order.
        "rgb888": (QImage.Format.Format_RGB888, 3),
        "bgr888": (QImage.Format.Format_BGR888, 3),
        "rgba8888": (QImage.Format.Format_RGBA8888, 4),
        "bgra8888": (QImage.Format.Format_ARGB32, 4),
        "gray8": (QImage.Format.Format_Grayscale8, 1),
        # Retain compatibility with frames used by older/test providers.
        "rgb24": (QImage.Format.Format_RGB888, 3),
        "bgr24": (QImage.Format.Format_BGR888, 3),
        "rgba": (QImage.Format.Format_RGBA8888, 4),
        "bgra": (QImage.Format.Format_ARGB32, 4),
    }
    layout = formats.get(frame.pixel_format.casefold())
    if layout is None:
        raise ValueError(
            f"Unsupported subtitle preview pixel format: {frame.pixel_format}"
        )
    image_format, bytes_per_pixel = layout
    minimum_stride = frame.width * bytes_per_pixel
    if frame.stride < minimum_stride:
        raise ValueError(
            "Invalid subtitle preview stride for "
            f"{frame.pixel_format}: {frame.stride} < {minimum_stride}"
        )
    image = QImage(
        frame.pixels,
        frame.width,
        frame.height,
        frame.stride,
        image_format,
    ).copy()
    if image.isNull():
        raise RuntimeError("The captured game frame could not be decoded")
    if (
        image.width() > REGION_PREVIEW_MAX_WIDTH
        or image.height() > REGION_PREVIEW_MAX_HEIGHT
    ):
        image = image.scaled(
            REGION_PREVIEW_MAX_WIDTH,
            REGION_PREVIEW_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(
        buffer, "PNG"
    ):
        raise RuntimeError("The captured game frame could not be encoded")
    buffer.close()
    payload = base64.b64encode(bytes(encoded)).decode("ascii")
    return EncodedRegionPreview(
        data_url=f"data:image/png;base64,{payload}",
        source_width=frame.width,
        source_height=frame.height,
        preview_width=image.width(),
        preview_height=image.height(),
    )


__all__ = [
    "EncodedRegionPreview",
    "PreviewContentRect",
    "encode_region_preview",
    "fitted_preview_content",
    "normalized_region_from_preview",
    "preview_rect_from_normalized",
]
