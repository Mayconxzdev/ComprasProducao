from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from app.core.dashboard_insights import event_title, event_type_label, human_datetime, recipients, response_summary, status_group
from app.qt.icon_utils import get_icon
from app.qt.models.tracking_list_model import TrackingListModel
from app.qt.ui_scale import scaled_px


def _quote_icon_key(kind: str) -> str:
    k = kind.casefold()
    if "frete" in k:
        return "freight"
    if "ordem" in k or "oc" in k:
        return "purchase_order"
    if "pain" in k or "ex" in k:
        return "ex_panels"
    return "material"


def _status_colors(status: str, dark: bool = False) -> tuple[QColor, QColor, QColor]:
    if dark:
        if status in {"Respondido", "Confirmado"}:
            return QColor("#113B2B"), QColor("#3CE58B"), QColor("#23895B")
        if status == "Sem cotação válida":
            return QColor("#173B5F"), QColor("#8CCBFF"), QColor("#3D77A6")
        if status == "Falha":
            return QColor("#421D24"), QColor("#FF8A94"), QColor("#A94E5B")
        if status == "Arquivado":
            return QColor("#263346"), QColor("#CBD5E1"), QColor("#53677F")
        return QColor("#3B2C14"), QColor("#F9C16D"), QColor("#956B2E")
    if status in {"Respondido", "Confirmado"}:
        return QColor("#E6F7ED"), QColor("#047857"), QColor("#C6ECD5")
    if status == "Sem cotação válida":
        return QColor("#EAF4FF"), QColor("#075C91"), QColor("#CBE2FF")
    if status == "Falha":
        return QColor("#FEE2E2"), QColor("#B91C1C"), QColor("#FECACA")
    if status == "Arquivado":
        return QColor("#F1F5F9"), QColor("#475569"), QColor("#E2E8F0")
    return QColor("#FFF3E7"), QColor("#C2410C"), QColor("#FED7AA")


class TrackingEventDelegate(QStyledItemDelegate):
    """Delegate visual do Acompanhar: card real, sem texto cortado nem widgets pesados."""

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), scaled_px(78))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        row = index.data(TrackingListModel.RowRole) or {}
        if not isinstance(row, dict):
            return super().paint(painter, option, index)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRect(option.rect).adjusted(scaled_px(6), scaled_px(4), -scaled_px(6), -scaled_px(4))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        dark = option.palette.color(QPalette.ColorRole.Window).lightness() < 128
        if dark:
            bg = QColor("#172438") if not hover else QColor("#1C2D44")
            if selected:
                bg = QColor("#1D3D5E") if not hover else QColor("#244B70")
            border = QColor("#5AA7FF") if selected else QColor("#36506E")
            title_color = QColor("#F7FAFC")
            meta_color = QColor("#C4D0DF")
            arrow_color = QColor("#8CB3D6")
        else:
            bg = QColor("#FFFFFF") if not hover else QColor("#FBFDFF")
            if selected:
                bg = QColor("#F4F9FF") if not hover else QColor("#EAF4FF")
            border = QColor("#0F6B99") if selected else QColor("#DDE7F1")
            title_color = QColor("#07142B")
            meta_color = QColor("#5F7088")
            arrow_color = QColor("#94A3B8")
        painter.setPen(QPen(border, 1.5 if selected else 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(r, scaled_px(18), scaled_px(18))

        kind_label = event_type_label(row)
        icon_key = _quote_icon_key(kind_label)
        if dark:
            icon_bg = QColor("#183B5C")
            icon_fg = QColor("#8CCBFF")
            if icon_key == "freight":
                icon_bg, icon_fg = QColor("#35245A"), QColor("#C4A5FF")
            elif icon_key == "purchase_order":
                icon_bg, icon_fg = QColor("#4A2815"), QColor("#FFB480")
            elif icon_key == "ex_panels":
                icon_bg, icon_fg = QColor("#143D2C"), QColor("#63E6A0")
        else:
            icon_bg = QColor("#EAF4FF")
            icon_fg = QColor("#0F6B99")
            if icon_key == "freight":
                icon_bg, icon_fg = QColor("#F3EAFE"), QColor("#7C3AED")
            elif icon_key == "purchase_order":
                icon_bg, icon_fg = QColor("#FFF0E5"), QColor("#EA580C")
            elif icon_key == "ex_panels":
                icon_bg, icon_fg = QColor("#E6F7ED"), QColor("#059669")

        icon_rect = QRect(r.left() + scaled_px(12), r.top() + scaled_px(18), scaled_px(36), scaled_px(36))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(icon_bg)
        painter.drawEllipse(icon_rect)
        try:
            icon = get_icon(icon_key, color=icon_fg.name(), scale_factor=0.95)
            if icon is not None and not icon.isNull():
                icon.paint(painter, icon_rect.adjusted(scaled_px(8), scaled_px(8), -scaled_px(8), -scaled_px(8)))
            else:
                raise RuntimeError("no icon")
        except Exception:
            painter.setPen(icon_fg)
            f = QFont(option.font); f.setBold(True); f.setPointSize(max(10, f.pointSize() + 2)); painter.setFont(f)
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, "■")

        status = status_group(row)
        pill_fill, pill_text, pill_border = _status_colors(status, dark)
        pf = QFont(option.font); pf.setBold(True); pf.setPointSize(max(8, pf.pointSize() - 1))
        fm_pill = QFontMetrics(pf)
        status_display = fm_pill.elidedText(status, Qt.TextElideMode.ElideRight, scaled_px(132))
        pill_w = min(max(scaled_px(86), fm_pill.horizontalAdvance(status_display) + scaled_px(20)), scaled_px(142))
        pill_rect = QRect(r.right() - pill_w - scaled_px(28), r.top() + scaled_px(22), pill_w, scaled_px(30))
        arrow_rect = QRect(r.right() - scaled_px(25), r.top(), scaled_px(20), r.height())

        x = icon_rect.right() + scaled_px(14)
        text_right = pill_rect.left() - scaled_px(12)
        text_w = max(scaled_px(90), text_right - x)

        title_font = QFont(option.font); title_font.setBold(True); title_font.setPointSize(max(10, title_font.pointSize()))
        painter.setFont(title_font); painter.setPen(title_color)
        title_rect = QRect(x, r.top() + scaled_px(14), text_w, scaled_px(23))
        title_text = QFontMetrics(title_font).elidedText(event_title(row), Qt.TextElideMode.ElideRight, title_rect.width())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)

        try:
            summary = response_summary(row, [])
            answered = int(summary.get("answered_count") or 0)
            total = int(summary.get("total") or len(recipients(row)) or 0)
        except Exception:
            answered, total = 0, len(recipients(row))
        meta = f"{kind_label} • {human_datetime(row.get('ts'))} • {answered}/{total} respondeu"
        meta_font = QFont(option.font); meta_font.setPointSize(max(8, meta_font.pointSize() - 1))
        painter.setFont(meta_font); painter.setPen(meta_color)
        meta_rect = QRect(x, r.top() + scaled_px(39), text_w, scaled_px(21))
        meta_text = QFontMetrics(meta_font).elidedText(meta, Qt.TextElideMode.ElideRight, meta_rect.width())
        painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta_text)

        painter.setPen(QPen(pill_border, 1.0))
        painter.setBrush(pill_fill)
        painter.drawRoundedRect(pill_rect, scaled_px(11), scaled_px(11))
        painter.setPen(pill_text)
        painter.setFont(pf)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, status_display)

        painter.setPen(arrow_color)
        af = QFont(option.font); af.setBold(True); af.setPointSize(max(12, af.pointSize() + 2)); painter.setFont(af)
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignCenter, "›")
        painter.restore()
