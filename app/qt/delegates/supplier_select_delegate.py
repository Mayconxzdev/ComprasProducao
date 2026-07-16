from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.qt.models import SupplierColumns


class SupplierSelectDelegate(QStyledItemDelegate):
    _BOX_SIZE = 16

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        if index.column() != SupplierColumns.SELECT:
            super().paint(painter, option, index)
            return

        checked_state = index.data(Qt.ItemDataRole.CheckStateRole)
        is_checked = checked_state == Qt.CheckState.Checked
        is_enabled = bool(option.state & QStyle.StateFlag.State_Enabled)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        box_rect = self._checkbox_rect(option.rect)
        radius = 3

        dark = option.palette.color(QPalette.ColorRole.Window).lightness() < 128
        if is_checked:
            fill = QColor("#5AA7FF" if dark else "#1268B3")
            border = QColor("#86C5FF" if dark else "#1268B3")
            check_color = QColor("#ffffff")
        else:
            fill = QColor("#101927" if dark else "#FFFFFF")
            border = QColor("#58708F" if dark else "#8EA9C8")
            check_color = QColor("#ffffff")

        if not is_enabled:
            fill.setAlpha(120)
            border.setAlpha(140)
            check_color.setAlpha(140)

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawRoundedRect(box_rect, radius, radius)

        if is_checked:
            self._draw_checkmark(painter, box_rect, check_color)

        painter.restore()

    def _checkbox_rect(self, cell_rect: QRect) -> QRect:
        x = cell_rect.x() + (cell_rect.width() - self._BOX_SIZE) // 2
        y = cell_rect.y() + (cell_rect.height() - self._BOX_SIZE) // 2
        return QRect(x, y, self._BOX_SIZE, self._BOX_SIZE)

    def _draw_checkmark(self, painter: QPainter, rect: QRect, color: QColor) -> None:
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        left = rect.left()
        top = rect.top()
        size = rect.width()
        p1x = left + int(size * 0.24)
        p1y = top + int(size * 0.55)
        p2x = left + int(size * 0.45)
        p2y = top + int(size * 0.76)
        p3x = left + int(size * 0.78)
        p3y = top + int(size * 0.30)
        painter.drawLine(p1x, p1y, p2x, p2y)
        painter.drawLine(p2x, p2y, p3x, p3y)
