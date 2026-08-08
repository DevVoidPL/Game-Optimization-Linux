"""Read-only Qt screen descriptions used by optimization profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class DisplayProfile:
    display_id: str
    name: str
    manufacturer: str
    model: str
    width: int
    height: int
    refresh_rate: float
    device_pixel_ratio: float
    primary: bool
    index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.display_id, "name": self.name,
            "manufacturer": self.manufacturer, "model": self.model,
            "width": self.width, "height": self.height,
            "refreshRate": self.refresh_rate,
            "devicePixelRatio": self.device_pixel_ratio,
            "primary": self.primary, "index": self.index,
            "label": f"{self.name} · {self.width}×{self.height} · {self.refresh_rate:.0f} Hz",
        }


class DisplayDetector:
    @staticmethod
    def _value(screen: Any, name: str, fallback: Any = "") -> Any:
        value = getattr(screen, name, fallback)
        try:
            return value() if callable(value) else value
        except Exception:
            return fallback

    def detect(self, screens: Iterable[Any], primary_screen: Any | None) -> tuple[DisplayProfile, ...]:
        result: list[DisplayProfile] = []
        for index, screen in enumerate(screens):
            name = str(self._value(screen, "name", f"Display {index + 1}") or f"Display {index + 1}")
            size = self._value(screen, "size", None)
            width = int(self._value(size, "width", 0)) if size is not None else 0
            height = int(self._value(size, "height", 0)) if size is not None else 0
            if width <= 0 or height <= 0:
                geometry = self._value(screen, "geometry", None)
                width = int(self._value(geometry, "width", 0))
                height = int(self._value(geometry, "height", 0))
            stable = f"screen-{index}:{name}"
            result.append(DisplayProfile(
                display_id=stable, name=name,
                manufacturer=str(self._value(screen, "manufacturer", "") or ""),
                model=str(self._value(screen, "model", "") or ""),
                width=max(width, 1), height=max(height, 1),
                refresh_rate=max(float(self._value(screen, "refreshRate", 60.0) or 60.0), 1.0),
                device_pixel_ratio=max(float(self._value(screen, "devicePixelRatio", 1.0) or 1.0), 0.1),
                primary=screen is primary_screen, index=index,
            ))
        return tuple(result)

    def from_application(self, application: Any) -> tuple[DisplayProfile, ...]:
        return self.detect(application.screens(), application.primaryScreen())


__all__ = ["DisplayDetector", "DisplayProfile"]
