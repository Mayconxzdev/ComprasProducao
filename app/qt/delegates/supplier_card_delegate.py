from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.qt.models import SupplierColumns


@dataclass(frozen=True)
class _CardText:
    products: str
    company: str
    contact: str
    email_phone: str


class SupplierCardDelegate(QStyledItemDelegate):
    _OUTER_MARGIN = 4
    _INNER_X = 10
    _INNER_Y = 8
    _LINE_GAP = 4
    _LINE_FLAGS = int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere)
    _CACHE_LIMIT = 4096

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._size_cache: dict[tuple[int, str], QSize] = {}

    def clear_size_cache(self) -> None:
        self._size_cache.clear()

    def _extract_texts(self, index) -> _CardText:
        model = index.model()
        products = str(
            model.data(
                index.siblingAtColumn(SupplierColumns.PRODUCTS),
                Qt.ItemDataRole.DisplayRole,
            )
            or "-"
        )
        company = str(
            model.data(
                index.siblingAtColumn(SupplierColumns.COMPANY),
                Qt.ItemDataRole.DisplayRole,
            )
            or "?"
        )
        contact = str(
            model.data(
                index.siblingAtColumn(SupplierColumns.CONTACT),
                Qt.ItemDataRole.DisplayRole,
            )
            or "-"
        )
        email = str(
            model.data(
                index.siblingAtColumn(SupplierColumns.EMAIL),
                Qt.ItemDataRole.DisplayRole,
            )
            or "-"
        )
        phone = str(
            model.data(
                index.siblingAtColumn(SupplierColumns.PHONE),
                Qt.ItemDataRole.DisplayRole,
            )
            or "-"
        )
        return _CardText(
            products=products,
            company=company,
            contact=contact,
            email_phone=f"{email} | {phone}",
        )

    def _content_key(self, text: _CardText) -> str:
        return "|".join([text.products, text.company, text.contact, text.email_phone])

    def _available_text_width(self, option) -> int:
        width = int(option.rect.width())
        if width <= 0:
            parent = self.parent()
            if parent is not None and hasattr(parent, "viewport"):
                viewport = parent.viewport()
                width = int(viewport.width())
        safe = width - (self._OUTER_MARGIN * 2) - (self._INNER_X * 2)
        return max(160, safe)

    def _measure_line_height(self, metrics: QFontMetrics, width: int, text: str) -> int:
        rect = metrics.boundingRect(QRect(0, 0, width, 32000), self._LINE_FLAGS, text)
        return max(metrics.height(), rect.height())

    def _size_for(self, option, text: _CardText) -> QSize:
        width = max(200, int(option.rect.width()))
        available = self._available_text_width(option)
        key = (available, self._content_key(text))
        cached = self._size_cache.get(key)
        if cached is not None:
            return cached

        normal_font = option.font
        bold_font = QFont(normal_font)
        bold_font.setBold(True)

        bold_metrics = QFontMetrics(bold_font)
        normal_metrics = QFontMetrics(normal_font)

        heights = [
            self._measure_line_height(bold_metrics, available, text.products),
            self._measure_line_height(bold_metrics, available, text.company),
            self._measure_line_height(normal_metrics, available, text.contact),
            self._measure_line_height(normal_metrics, available, text.email_phone),
        ]
        total_text = sum(heights)
        total_gap = self._LINE_GAP * (len(heights) - 1)
        total = (self._OUTER_MARGIN * 2) + (self._INNER_Y * 2) + total_text + total_gap
        size = QSize(width, max(88, total))

        if len(self._size_cache) >= self._CACHE_LIMIT:
            self._size_cache.clear()
        self._size_cache[key] = size
        return size

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        text = self._extract_texts(index)
        model = index.model()
        checked_state = model.data(
            index.siblingAtColumn(SupplierColumns.SELECT),
            Qt.ItemDataRole.CheckStateRole,
        )
        is_checked = checked_state == Qt.CheckState.Checked
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        card_rect = option.rect.adjusted(
            self._OUTER_MARGIN,
            self._OUTER_MARGIN,
            -self._OUTER_MARGIN,
            -self._OUTER_MARGIN,
        )

        dark = option.palette.color(QPalette.ColorRole.Window).lightness() < 128
        if dark:
            bg_normal = QColor("#141E2C")
            bg_hover = QColor("#20304A")
            bg_checked = QColor("#183B5C")
            border_normal = QColor("#2A3B53")
            border_focus = QColor("#5AA7FF")
        else:
            bg_normal = QColor("#ffffff")
            bg_hover = QColor("#F2F7FF")
            bg_checked = QColor("#E8F2FF")
            border_normal = QColor("#D6E1EE")
            border_focus = QColor("#1268B3")

        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if is_checked:
            bg = bg_checked
            border = border_focus
        elif is_selected or is_hover:
            bg = bg_hover
            border = border_focus if is_selected else border_normal
        else:
            bg = bg_normal
            border = border_normal

        painter.setPen(border)
        painter.setBrush(bg)
        painter.drawRoundedRect(card_rect, 9, 9)

        available = self._available_text_width(option)
        text_left = card_rect.left() + self._INNER_X
        text_top = card_rect.top() + self._INNER_Y
        text_right = text_left + available

        normal_font = option.font
        bold_font = QFont(normal_font)
        bold_font.setBold(True)

        if dark:
            primary = QColor("#F7FAFC")
            accent = QColor("#5AA7FF")
            muted = QColor("#C4D0DF")
        else:
            primary = QColor("#071A33")
            accent = QColor("#1268B3")
            muted = QColor("#51657F")

        rows = [
            (text.company, primary, bold_font),
            (text.products, accent, bold_font),
            (text.contact, muted, normal_font),
            (text.email_phone, muted, normal_font),
        ]

        y = text_top
        for idx_row, (line_text, color, font) in enumerate(rows):
            painter.setFont(font)
            metrics = QFontMetrics(font)
            line_h = self._measure_line_height(metrics, available, line_text)
            rect = QRect(text_left, y, text_right - text_left, line_h)
            painter.setPen(color)
            painter.drawText(rect, self._LINE_FLAGS, line_text)
            y += line_h
            if idx_row < len(rows) - 1:
                y += self._LINE_GAP

        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        text = self._extract_texts(index)
        return self._size_for(option, text)
