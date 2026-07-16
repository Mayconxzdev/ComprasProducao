from __future__ import annotations

from PySide6.QtGui import QFont


DEFAULT_POINT_SIZE = 10.0


def ensure_valid_font(font: QFont, *, default_point_size: float = DEFAULT_POINT_SIZE) -> QFont:
    """
    Return a copy-safe valid QFont for widgets where platform/theme can provide
    invalid values (pointSize <= 0 and pixelSize <= 0).
    """
    safe_font = QFont(font)
    point_size = float(safe_font.pointSizeF())
    pixel_size = int(safe_font.pixelSize())
    if point_size > 0 or pixel_size > 0:
        return safe_font
    safe_font.setPointSizeF(max(1.0, float(default_point_size)))
    return safe_font
