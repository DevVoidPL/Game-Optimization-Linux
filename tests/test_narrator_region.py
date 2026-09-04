from __future__ import annotations

import base64
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from game_optimization_linux.models.narrator import CaptureFrame, NormalizedRect
from game_optimization_linux.services.narrator_region import (
    encode_region_preview,
    fitted_preview_content,
    normalized_region_from_preview,
    preview_rect_from_normalized,
)


def test_narrator_page_uses_native_selector_instead_of_qml_popup() -> None:
    root = Path(__file__).resolve().parents[1]
    obsolete_selector = (
        root
        / "src/game_optimization_linux/qml/dialogs/SubtitleRegionSelector.qml"
    )
    page = (
        root / "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    ).read_text(encoding="utf-8")
    native = (
        root
        / "src/game_optimization_linux/controllers/narrator_region_selector.py"
    ).read_text(encoding="utf-8")
    bootstrap = (
        root / "src/game_optimization_linux/app.py"
    ).read_text(encoding="utf-8")

    assert not obsolete_selector.exists()
    assert "SubtitleRegionSelector" not in page
    assert "selectNarratorSubtitleRegion" in page
    assert "class RegionCanvas(QWidget)" in native
    assert "def mousePressEvent" in native
    assert "def mouseMoveEvent" in native
    assert "def mouseReleaseEvent" in native
    assert "MouseArea" not in native
    assert "PointHandler" not in native
    assert "from PySide6.QtWidgets import QApplication" in bootstrap
    assert "application = QApplication(qt_arguments)" in bootstrap
    assert "controller.attach_main_window(engine.rootObjects()[0])" in bootstrap


def test_normalized_coordinate_conversion_without_scaling() -> None:
    region = normalized_region_from_preview(
        source_width=1920,
        source_height=1080,
        viewport_width=1920,
        viewport_height=1080,
        selection_x=192,
        selection_y=648,
        selection_width=1344,
        selection_height=216,
    )

    assert region == NormalizedRect(x=0.1, y=0.6, width=0.7, height=0.2)


def test_letterboxed_preview_maps_only_the_painted_game_frame() -> None:
    content = fitted_preview_content(1920, 1080, 1000, 1000)
    assert content.x == pytest.approx(0)
    assert content.y == pytest.approx(218.75)
    assert content.width == pytest.approx(1000)
    assert content.height == pytest.approx(562.5)

    region = normalized_region_from_preview(
        source_width=1920,
        source_height=1080,
        viewport_width=1000,
        viewport_height=1000,
        selection_x=100,
        selection_y=318.75,
        selection_width=700,
        selection_height=200,
    )

    assert region.x == pytest.approx(0.1)
    assert region.y == pytest.approx(100 / 562.5)
    assert region.width == pytest.approx(0.7)
    assert region.height == pytest.approx(200 / 562.5)


def test_pillarboxed_preview_clamps_selection_to_frame_edges() -> None:
    content = fitted_preview_content(1080, 1920, 1000, 500)
    assert content.x == pytest.approx(359.375)
    assert content.y == pytest.approx(0)

    region = normalized_region_from_preview(
        source_width=1080,
        source_height=1920,
        viewport_width=1000,
        viewport_height=500,
        selection_x=-100,
        selection_y=50,
        selection_width=1200,
        selection_height=400,
    )

    assert region == NormalizedRect(x=0, y=0.1, width=1, height=0.8)


def test_normalized_region_round_trips_through_scaled_preview() -> None:
    expected = NormalizedRect(x=0.17, y=0.61, width=0.68, height=0.22)
    x, y, width, height = preview_rect_from_normalized(
        expected,
        source_width=2560,
        source_height=1440,
        viewport_width=940,
        viewport_height=700,
    )

    actual = normalized_region_from_preview(
        source_width=2560,
        source_height=1440,
        viewport_width=940,
        viewport_height=700,
        selection_x=x,
        selection_y=y,
        selection_width=width,
        selection_height=height,
    )

    assert actual.x == pytest.approx(expected.x)
    assert actual.y == pytest.approx(expected.y)
    assert actual.width == pytest.approx(expected.width)
    assert actual.height == pytest.approx(expected.height)


@pytest.mark.parametrize(
    ("width", "height"),
    ((0, 100), (100, 0), (0.1, 0.1)),
)
def test_zero_or_subpixel_selection_is_rejected(width: float, height: float) -> None:
    with pytest.raises(ValueError, match="non-empty|too small"):
        normalized_region_from_preview(
            source_width=1920,
            source_height=1080,
            viewport_width=960,
            viewport_height=540,
            selection_x=100,
            selection_y=100,
            selection_width=width,
            selection_height=height,
        )


def test_capture_frame_preview_is_in_memory_png_with_source_dimensions() -> None:
    frame = CaptureFrame(
        session_id="preview",
        generation=1,
        timestamp_monotonic=1.0,
        width=4,
        height=2,
        stride=12,
        pixel_format="rgb24",
        pixels=bytes((255, 0, 0) * 8),
        source_id="fixture-window",
    )

    preview = encode_region_preview(frame)

    assert preview.source_width == 4
    assert preview.source_height == 2
    assert preview.preview_width == 4
    assert preview.preview_height == 2
    prefix = "data:image/png;base64,"
    assert preview.data_url.startswith(prefix)
    assert base64.b64decode(preview.data_url.removeprefix(prefix)).startswith(
        b"\x89PNG\r\n\x1a\n"
    )


def test_rgb888_preview_preserves_channel_order_and_padded_stride() -> None:
    # Two RGB pixels plus two padding bytes on each row. Padding must not be
    # interpreted as image data or shift the following row.
    pixels = bytes(
        (
            255, 0, 0,  # red
            0, 255, 0,  # green
            17, 23,  # row padding
            0, 0, 255,  # blue
            255, 255, 255,  # white
            31, 47,  # row padding
        )
    )
    frame = CaptureFrame(
        session_id="rgb888-preview",
        generation=1,
        timestamp_monotonic=1.0,
        width=2,
        height=2,
        stride=8,
        pixel_format="rgb888",
        pixels=pixels,
        source_id="pipewire-window",
    )

    preview = encode_region_preview(frame)
    encoded = base64.b64decode(preview.data_url.partition(",")[2])
    decoded = QImage.fromData(encoded, "PNG")

    assert not decoded.isNull()
    assert (decoded.width(), decoded.height()) == (2, 2)
    assert decoded.pixelColor(0, 0) == QColor(255, 0, 0)
    assert decoded.pixelColor(1, 0) == QColor(0, 255, 0)
    assert decoded.pixelColor(0, 1) == QColor(0, 0, 255)
    assert decoded.pixelColor(1, 1) == QColor(255, 255, 255)


def test_rgb888_preview_rejects_stride_shorter_than_one_pixel_row() -> None:
    frame = CaptureFrame(
        session_id="bad-rgb888-preview",
        generation=1,
        timestamp_monotonic=1.0,
        width=2,
        height=1,
        stride=5,
        pixel_format="rgb888",
        pixels=b"\0" * 5,
    )

    with pytest.raises(ValueError, match="Invalid subtitle preview stride"):
        encode_region_preview(frame)
