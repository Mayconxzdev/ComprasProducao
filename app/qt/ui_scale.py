from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


_PX_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)px")
_BASELINE_WIDTH = 1600
_BASELINE_HEIGHT = 900
_MIN_SCALE = 0.78
_MAX_SCALE = 1.0


@dataclass(frozen=True)
class UiScaleProfile:
    scale: float
    available_width: int
    available_height: int
    compact: bool


_PROFILE = UiScaleProfile(scale=1.0, available_width=_BASELINE_WIDTH, available_height=_BASELINE_HEIGHT, compact=False)


def build_scale_profile(width: int, height: int) -> UiScaleProfile:
    safe_width = max(800, int(width or _BASELINE_WIDTH))
    safe_height = max(600, int(height or _BASELINE_HEIGHT))
    width_factor = safe_width / _BASELINE_WIDTH
    height_factor = safe_height / _BASELINE_HEIGHT
    scale = min(width_factor, height_factor, _MAX_SCALE)
    scale = max(_MIN_SCALE, scale)
    compact = safe_width < 1450 or safe_height < 820
    return UiScaleProfile(scale=scale, available_width=safe_width, available_height=safe_height, compact=compact)


def init_ui_scale(app: QApplication) -> UiScaleProfile:
    global _PROFILE  # noqa: PLW0603

    screen = app.primaryScreen() or QGuiApplication.primaryScreen()
    if screen is None:
        _PROFILE = build_scale_profile(_BASELINE_WIDTH, _BASELINE_HEIGHT)
        return _PROFILE
    geometry = screen.availableGeometry()
    _PROFILE = build_scale_profile(geometry.width(), geometry.height())
    return _PROFILE


def ui_scale_profile() -> UiScaleProfile:
    return _PROFILE


def scaled_px(value: int | float) -> int:
    numeric = float(value)
    if numeric <= 0:
        return 0
    return max(1, int(round(numeric * _PROFILE.scale)))


def scaled_window_size(default_width: int, default_height: int, *, min_width: int = 800, min_height: int = 600) -> tuple[int, int]:
    width = scaled_px(default_width)
    height = scaled_px(default_height)
    max_width = max(scaled_px(min_width), _PROFILE.available_width - scaled_px(40))
    max_height = max(scaled_px(min_height), _PROFILE.available_height - scaled_px(40))
    return min(width, max_width), min(height, max_height)


def font_css(size_px: int, weight: int | str) -> str:
    return f"font-size: {scaled_px(size_px)}px; font-weight: {weight};"


def scale_stylesheet_px(stylesheet: str) -> str:
    if _PROFILE.scale == 1.0:
        return stylesheet

    def _replace(match: re.Match[str]) -> str:
        original = float(match.group("value"))
        if original <= 0:
            return "0px"
        return f"{scaled_px(original)}px"

    return _PX_RE.sub(_replace, stylesheet)
