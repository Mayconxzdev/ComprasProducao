from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QComboBox, QListView, QStyledItemDelegate, QStyleOptionViewItem

from app.qt.ui_scale import scaled_px
from app.qt.theme import ensure_valid_font


class VesperSelectItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), scaled_px(38))


class VesperSelect(QComboBox):
    """QComboBox com popup controlado para evitar visual nativo quebrado no Windows."""

    def __init__(self, parent=None, *, visible_rows: int = 5) -> None:
        super().__init__(parent)
        self._visible_rows = max(1, int(visible_rows or 5))
        self.setObjectName("topCombo")
        self.setEditable(False)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)
        self.setFont(ensure_valid_font(self.font()))
        view = QListView(self)
        view.setObjectName("comboPopup")
        view.setUniformItemSizes(True)
        view.setItemDelegate(VesperSelectItemDelegate(view))
        view.setAlternatingRowColors(False)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setFont(ensure_valid_font(view.font()))
        self.setView(view)
        self.setMaxVisibleItems(self._visible_rows)
        self.setMinimumWidth(scaled_px(230))
        self.setMinimumHeight(scaled_px(42))
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def showPopup(self) -> None:  # noqa: N802
        view = self.view()
        if view is not None:
            row_h = scaled_px(38)
            rows = min(max(1, self.count()), self._visible_rows)
            width = max(self.width(), self.minimumWidth())
            view.setMinimumWidth(width)
            view.setMaximumWidth(width)
            view.setMinimumHeight(row_h)
            view.setMaximumHeight(row_h * rows + scaled_px(10))
            try:
                view.window().setFixedWidth(width)
            except Exception:
                pass
        super().showPopup()

    def paintEvent(self, event) -> None:  # noqa: N802
        # Mantém o desenho padrão, mas substitui a seta nativa quebrada do Windows
        # por um chevron estável e compatível com claro/escuro.
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            color = self.palette().color(QPalette.ColorRole.Text)
            painter.setPen(color)
            fm = QFontMetrics(self.font())
            symbol = "▾"
            x = self.width() - scaled_px(24)
            y = int((self.height() + fm.ascent() - fm.descent()) / 2)
            painter.drawText(x, y, symbol)
        finally:
            painter.end()
