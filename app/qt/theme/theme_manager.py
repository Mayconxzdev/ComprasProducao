from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLayout

from app.qt.ui_scale import scale_stylesheet_px


@dataclass(frozen=True)
class _ThemePalette:
    window: str
    surface: str
    surface_subtle: str
    text: str
    text_muted: str
    accent: str
    accent_soft: str
    border: str
    border_strong: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    input_bg: str
    hover: str
    selected_text: str = "#FFFFFF"


_LIGHT = _ThemePalette(
    window="#F7F9FC",
    surface="#FFFFFF",
    surface_subtle="#F9FBFF",
    text="#10213A",
    text_muted="#5A6B84",
    accent="#0F5DA8",
    accent_soft="#E8F2FF",
    border="#D7E1EE",
    border_strong="#AFC7E6",
    success="#117A37",
    success_bg="#E8F7EE",
    warning="#B86A00",
    warning_bg="#FFF4E3",
    danger="#B42318",
    danger_bg="#FEECEC",
    input_bg="#FFFFFF",
    hover="#F1F6FF",
)

_DARK = _ThemePalette(
    # Tema escuro 3.6: slate/azul-petróleo com contraste AA/AAA nos
    # componentes operacionais. Evita preto puro, evita texto claro sobre
    # card claro e mantém hover/seleção legíveis.
    window="#0F1724",
    surface="#141E2C",
    surface_subtle="#19263A",
    text="#F7FAFC",
    text_muted="#C4D0DF",
    accent="#5AA7FF",
    accent_soft="#183B5C",
    border="#2A3B53",
    border_strong="#58708F",
    success="#3CE58B",
    success_bg="#113B2B",
    warning="#F9C16D",
    warning_bg="#3B2C14",
    danger="#FF8A94",
    danger_bg="#421D24",
    input_bg="#101927",
    hover="#20304A",
    selected_text="#0B1220",
)


class ThemeManager:
    """Apply and persist app-wide light/dark themes."""

    SETTINGS_ORG = "ComprasVesper"
    SETTINGS_APP = "ComprasVesper"
    SETTINGS_KEY = "ui/theme_mode"
    QSS_PATH = Path(__file__).resolve().parents[2] / "assets" / "theme" / "app.qss"

    def __init__(self, app: QApplication):
        self.app = app
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._mode = "light"
        self._active = "light"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def active_theme(self) -> str:
        return self._active

    def apply_system_theme(self) -> None:
        mode = str(self.settings.value(self.SETTINGS_KEY, "light") or "light").strip().lower()
        if mode not in {"dark", "light"}:
            mode = "light"
        self.set_theme(mode)

    def set_theme(self, mode: str) -> None:
        mode = str(mode or "light").strip().lower()
        if mode not in {"dark", "light"}:
            mode = "light"
        self._mode = mode
        self._active = mode
        self.settings.setValue(self.SETTINGS_KEY, mode)
        self._apply_palette(mode)

    def toggle_theme(self) -> None:
        self.cycle_mode()

    def cycle_mode(self) -> str:
        self.set_theme("light" if self._mode == "dark" else "dark")
        return self._mode

    def mode_label(self) -> str:
        return "Escuro" if self._mode == "dark" else "Claro"

    def repolish(self) -> None:
        self._apply_global_layout_metrics()
        for widget in self.app.allWidgets():
            try:
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
            except Exception:
                continue

    def _apply_global_layout_metrics(self) -> None:
        """Padroniza respiro interno de cards em todas as telas.

        QSS resolve cor/borda, mas os filhos de um QFrame obedecem às margens
        do layout Python. Sem isso, alguns cards ficam com texto colado na borda
        em claro/escuro e passam impressão de beta. Esta etapa é idempotente e
        só aumenta margens muito pequenas.
        """

        card_names = {
            "dashboardCard", "dashboardHero", "dashboardFooter", "pageCard", "finalCard",
            "summaryPanel", "trackingContextPanel", "subtlePanel", "customFieldsCard", "advancedSettingsPanel",
            "dropZone", "dropZonePrimary", "signaturePreview", "identityBar", "sendBar",
            "responseCard", "analysisCard", "trackingRecipientsPanel", "trackingListPanel",
        }
        row_names = {
            "trackingRow", "recipientRow", "modelRow", "signatureRow", "exTemplateRow",
            "exPanelCard", "specInputRow", "actionBar", "topControlWrap",
        }
        compact_names = {"summaryCell", "embeddedIntro"}

        def ensure(layout: QLayout, left: int, top: int, right: int, bottom: int, spacing: int) -> None:
            try:
                m = layout.contentsMargins()
                layout.setContentsMargins(
                    max(m.left(), left),
                    max(m.top(), top),
                    max(m.right(), right),
                    max(m.bottom(), bottom),
                )
                if layout.spacing() < spacing:
                    layout.setSpacing(spacing)
            except Exception:
                return

        for widget in self.app.allWidgets():
            try:
                name = widget.objectName()
                layout = widget.layout()
            except Exception:
                continue
            if layout is None:
                continue
            if name in card_names:
                ensure(layout, 18, 16, 18, 16, 10)
            elif name == "topControlWrap":
                ensure(layout, 12, 9, 12, 10, 6)
            elif name in row_names:
                ensure(layout, 12, 8, 12, 8, 8)
            elif name in compact_names:
                ensure(layout, 14, 10, 14, 10, 6)

    def _apply_palette(self, active: str) -> None:
        p = _DARK if active == "dark" else _LIGHT
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(p.window))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
        palette.setColor(QPalette.ColorRole.Base, QColor(p.input_bg))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface_subtle))
        palette.setColor(QPalette.ColorRole.Text, QColor(p.text))
        palette.setColor(QPalette.ColorRole.Button, QColor(p.surface_subtle))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(p.selected_text))
        palette.setColor(QPalette.ColorRole.Link, QColor(p.accent))
        palette.setColor(QPalette.ColorRole.Mid, QColor(p.border))
        palette.setColor(QPalette.ColorRole.Midlight, QColor(p.border_strong))
        palette.setColor(QPalette.ColorRole.Dark, QColor(p.surface_subtle))
        palette.setColor(QPalette.ColorRole.Light, QColor(p.surface_subtle))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
        self.app.setPalette(palette)

        colors = {
            "{WINDOW}": p.window,
            "{CARD}": p.surface,
            "{CARD_ALT}": p.surface_subtle,
            "{TEXT}": p.text,
            "{TEXT_MUTED}": p.text_muted,
            "{ACCENT}": p.accent,
            "{BORDER}": p.border,
            "{SUCCESS}": p.success,
            "{DANGER}": p.danger,
        }
        stylesheet = self._load_qss_template()
        for token, value in colors.items():
            stylesheet = stylesheet.replace(token, value)
        stylesheet += "\n" + self._final_override(p)
        self.app.setStyleSheet(scale_stylesheet_px(stylesheet))
        self._apply_global_layout_metrics()

    def _load_qss_template(self) -> str:
        try:
            if self.QSS_PATH.exists():
                return self.QSS_PATH.read_text(encoding="utf-8")
        except Exception:
            pass
        return """
        QWidget { background: {WINDOW}; color: {TEXT}; font-family: "Segoe UI", "Arial"; font-size: 13px; }
        """

    def _final_override(self, p: _ThemePalette) -> str:
        css = """
/* ComprasVesper 4.1.0 — refinamento global responsivo e scrolls internos */
QWidget#appRoot, QWidget#mainContentSurface, QScrollArea#modeScroll, QWidget#modePage, QWidget#dashboardContainer {
    background: @WINDOW@;
    color: @TEXT@;
}
QFrame#topBar, QFrame#sideNav, QFrame#dashboardHero, QFrame#dashboardCard, QFrame#dashboardFooter,
QFrame#finalContent, QFrame#finalCard, QFrame#modePage, QFrame#pageCard, QFrame#summaryPanel,
QFrame#summaryCell, QFrame#trackingContextPanel, QFrame#responseCard, QFrame#analysisCard,
QFrame#actionBar, QFrame#recipientRow, QFrame#trackingRow, QFrame#modelRow, QFrame#signatureRow,
QFrame#signaturePreview, QFrame#customFieldsCard, QFrame#subtlePanel, QFrame#topControlWrap,
QFrame#dropZone, QFrame#dropZonePrimary, QFrame#exPanelCard, QFrame#exTemplateRow,
QFrame#quoteType_blue, QFrame#quoteType_green, QFrame#quoteType_purple, QFrame#quoteType_orange,
QFrame#dashAction_blue, QFrame#dashAction_green, QFrame#dashAction_purple, QFrame#dashAction_orange,
QFrame#dashAction_cyan, QFrame#dashAction_indigo, QFrame#dashAction_slate, QFrame#dashAction_red {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 14px;
}
QFrame#topBar { border-left: 0; border-right: 0; border-top: 0; border-radius: 0; }
QFrame#sideNav { border-left: 0; border-top: 0; border-bottom: 0; border-radius: 0; }
QLabel, QCheckBox, QRadioButton { color: @TEXT@; background: transparent; }
QLabel#muted, QLabel#pageSubtitle, QLabel#heroSubtitle, QLabel#recipientEmail, QLabel#topControlLabel { color: @MUTED@; }
QLabel#pageTitle, QLabel#heroTitle, QLabel#dashCardTitle, QLabel#cardTitle, QLabel#cardTitleSmall,
QLabel#recentTitle, QLabel#recipientName, QLabel#summaryValue, QLabel#brandTitle { color: @TEXT@; }
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QListView, QListWidget, QTableView, QTableWidget,
QComboBox, QComboBox#topCombo, QComboBox QAbstractItemView {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
    selection-background-color: @ACCENT@;
    selection-color: @SELECTED_TEXT@;
}
QTextBrowser#responseBrowser, QTextBrowser#analysisBrowser, QTextBrowser#previewBrowser, QTextEdit#signaturePreview {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus, QListView:focus, QTableView:focus, QComboBox:focus {
    border: 1px solid @ACCENT@;
}
QComboBox::drop-down { width: 30px; border: 0; background: transparent; }
QComboBox::down-arrow { image: none; width: 0px; height: 0px; }
QComboBox QAbstractItemView::item { min-height: 34px; padding: 7px 10px; color: @TEXT@; background: @SURFACE@; }
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected { background: @ACCENT_SOFT@; color: @TEXT@; }
QPushButton, QToolButton {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
    padding: 8px 12px;
    font-weight: 650;
}
QPushButton:hover, QToolButton:hover, QPushButton#secondarySmall:hover, QPushButton#secondaryAction:hover,
QPushButton#topLinkButton:hover, QPushButton#topMoreButton:hover { background: @HOVER@; border-color: @BORDER_STRONG@; }
QPushButton#accent, QPushButton#sideNavButton:checked, QPushButton#filterChip:checked, QPushButton#segmentButton:checked,
QPushButton#companyPill:checked, QPushButton#dashArrow, QPushButton#linkButton {
    background: @ACCENT@;
    color: @SELECTED_TEXT@;
    border-color: @ACCENT@;
}
QPushButton#sideNavButton, QPushButton#filterChip, QPushButton#secondarySmall, QPushButton#secondaryAction,
QPushButton#topLinkButton, QPushButton#topMoreButton { background: @SURFACE@; color: @TEXT@; }
QPushButton#sideNavButton:hover, QPushButton#sideNavButton:checked { background: @ACCENT_SOFT@; color: @ACCENT@; border-color: @BORDER_STRONG@; }
QHeaderView::section { background: @SURFACE_SUBTLE@; color: @TEXT@; border: 0; border-bottom: 1px solid @BORDER@; padding: 8px; }
QTableView { background: @SURFACE@; alternate-background-color: @SURFACE_SUBTLE@; gridline-color: @BORDER@; }
QTableView::item, QTableWidget::item { background: @SURFACE@; color: @TEXT@; }
QTableView::item:alternate, QTableWidget::item:alternate { background: @SURFACE_SUBTLE@; color: @TEXT@; }
QListView#trackingList, QListWidget#trackingList, QListWidget#recipientList, QListWidget#exPanelList, QListWidget#exTemplateList { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 12px; }
QListWidget::item { background: transparent; color: @TEXT@; }
QListWidget::item:selected { background: @ACCENT_SOFT@; color: @TEXT@; }

QListWidget#recipientList::item {
    background: transparent;
    color: @TEXT@;
    padding: 7px 10px;
    margin: 3px 2px;
    border: 1px solid transparent;
    border-radius: 11px;
}
QListWidget#recipientList::item:hover {
    background: @HOVER@;
    color: @TEXT@;
    border-color: @BORDER_STRONG@;
}
QListWidget#recipientList::item:selected, QListWidget#recipientList::item:selected:active, QListWidget#recipientList::item:selected:!active {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
    border-color: @ACCENT@;
}
QLabel#statusOk, QLabel#softBadge { color: @SUCCESS@; background: @SUCCESS_BG@; border: 1px solid @SUCCESS@; border-radius: 9px; padding: 5px 10px; }
QLabel#statusWarn { color: @WARNING@; background: @WARNING_BG@; border: 1px solid @WARNING@; border-radius: 9px; padding: 5px 10px; }
QLabel#statusError { color: @DANGER@; background: @DANGER_BG@; border: 1px solid @DANGER@; border-radius: 9px; padding: 5px 10px; }
QLabel#statusInfo, QLabel#nextAction { color: @ACCENT@; background: @ACCENT_SOFT@; border: 1px solid @BORDER_STRONG@; border-radius: 9px; padding: 5px 10px; }
QTabWidget::pane { border: 1px solid @BORDER@; background: @SURFACE@; border-radius: 12px; }
QTabBar::tab { background: @SURFACE@; color: @TEXT@; border: 1px solid @BORDER@; border-radius: 10px; padding: 8px 18px; margin-right: 6px; }
QTabBar::tab:selected { background: @ACCENT_SOFT@; color: @ACCENT@; border-color: @BORDER_STRONG@; }
QMenu { background: @SURFACE@; color: @TEXT@; border: 1px solid @BORDER@; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 20px; border-radius: 7px; }
QMenu::item:selected { background: @ACCENT_SOFT@; color: @TEXT@; }
QScrollBar:vertical, QScrollBar:horizontal { background: @SURFACE@; border: 0; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: @BORDER_STRONG@; border-radius: 5px; min-height: 28px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }

QSplitter#trackingMainSplitter, QSplitter#trackingLeftSplitter, QSplitter#responseDetailSplitter {
    background: @WINDOW@;
    border: 0;
}
QSplitter#trackingMainSplitter::handle, QSplitter#trackingLeftSplitter::handle, QSplitter#responseDetailSplitter::handle {
    background: @BORDER@;
    border: 0;
}
QSplitter#trackingMainSplitter::handle:hover, QSplitter#trackingLeftSplitter::handle:hover, QSplitter#responseDetailSplitter::handle:hover {
    background: @ACCENT@;
}
QFrame#trackingListPanel, QFrame#trackingContextPanel, QFrame#responseCard, QFrame#analysisCard {
    background: @SURFACE_SUBTLE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 13px;
}
QFrame#analysisCard QTextBrowser#analysisBrowser {
    background: @SURFACE_SUBTLE@;
    color: @TEXT@;
    border: 0;
}
QFrame#responseCard QTextBrowser#responseBrowser {
    background: @INPUT@;
    color: @TEXT@;
    border: 0;
}

QPushButton#primaryAction {
    background: @ACCENT@;
    color: @SELECTED_TEXT@;
    border: 1px solid @ACCENT@;
    border-radius: 12px;
    padding: 9px 18px;
    font-weight: 900;
}
QPushButton#primaryAction:hover { background: @ACCENT_HOVER@; border-color: @ACCENT_HOVER@; }
QPushButton#primaryAction:disabled { background: @SURFACE_SUBTLE@; color: @MUTED@; border-color: @BORDER@; }
QPushButton#quietAction {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER_STRONG@;
    border-radius: 11px;
    padding: 7px 14px;
    font-weight: 800;
}
QPushButton#quietAction:hover { background: @HOVER@; color: @TEXT@; }
QPushButton#quietAction:disabled { background: @SURFACE_SUBTLE@; color: @MUTED@; border-color: @BORDER@; }

QFrame#finalHeader, QFrame#identityBar, QFrame#sendBar { background: @SURFACE@; color: @TEXT@; border-color: @BORDER@; }
QFrame#finalHeader { border-radius: 0px; border-left: 0; border-right: 0; border-top: 0; }
QFrame#sendBar { border-radius: 12px; }

/* Propriedade usada pelo shell atual; evita o botão ativo claro no tema escuro. */
QPushButton#sideNavButton[active="true"] {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
    border-color: @BORDER_STRONG@;
    font-weight: 900;
}
QPushButton#sideNavButton[active="true"]:hover { background: @HOVER@; color: @TEXT@; }
QFrame#tipCard, QFrame#dashboardCard, QFrame#dashboardFooter, QFrame#pageCard, QFrame#finalCard {
    background: @SURFACE@;
    border-color: @BORDER@;
}
QTextBrowser#responseBrowser, QTextBrowser#previewBrowser, QTextEdit#signaturePreview, QTextEdit#smartInput,
QPlainTextEdit, QTextEdit, QTextBrowser {
    background: @INPUT@;
    color: @TEXT@;
    border-color: @BORDER@;
}
QTextBrowser#responseBrowser { padding: 10px; }
QListView#trackingList::item:selected, QListWidget#recipientList::item:selected, QTableView::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}
QFrame#quoteType_blue, QFrame#quoteType_green, QFrame#quoteType_purple, QFrame#quoteType_orange {
    background: @SURFACE_SUBTLE@;
}


/* Polimento 3.5: cards de resumo e área de ações com respiro interno. */
QFrame#summaryPanel {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 14px;
}
QFrame#summaryCell {
    background: @SURFACE_SUBTLE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 11px;
}
QFrame#summaryCell:hover {
    background: @HOVER@;
    border-color: @BORDER_STRONG@;
}
QFrame#summaryCell QLabel#summaryValue,
QFrame#summaryCell QLabel#muted {
    background: transparent;
}
QFrame#actionBar {
    background: @SURFACE@;
    border: 1px solid @BORDER@;
    border-radius: 13px;
}
QTextBrowser#responseBrowser {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 12px;
    padding: 12px;
}
QTextBrowser#responseBrowser:focus {
    border-color: @ACCENT@;
}

/* Estados P0 de seleção: linha/card inteiro clicável com contraste garantido. */
QFrame#recipientRow {
    background: @SURFACE_SUBTLE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 12px;
}
QFrame#recipientRow:hover {
    background: @HOVER@;
    border-color: @BORDER_STRONG@;
}
QFrame#recipientRow[checked="true"] {
    background: @ACCENT_SOFT@;
    border-color: @ACCENT@;
}
QFrame#recipientRow[checked="true"]:hover {
    background: @ACCENT_SOFT@;
    border-color: @ACCENT@;
}
QFrame#recipientRow QLabel#recipientName { color: @TEXT@; font-weight: 800; }
QFrame#recipientRow QLabel#recipientEmail { color: @MUTED@; }
QFrame#recipientRow:hover QLabel#recipientName { color: @TEXT@; }
QFrame#recipientRow:hover QLabel#recipientEmail { color: @TEXT@; }
QFrame#recipientRow[checked="true"] QLabel#recipientName { color: @TEXT@; }
QFrame#recipientRow[checked="true"] QLabel#recipientEmail { color: @TEXT@; }
QFrame#recipientRow[checked="true"] QCheckBox { color: @TEXT@; }
QFrame#recipientRow QPushButton#iconRemove {
    background: transparent;
    color: @TEXT@;
    border: 0;
    font-size: 18px;
    padding: 0px;
}
QFrame#recipientRow QPushButton#iconRemove:hover {
    background: @DANGER_BG@;
    color: @DANGER@;
    border: 1px solid @DANGER@;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid @BORDER_STRONG@;
    background: @INPUT@;
}
QCheckBox::indicator:hover { border-color: @ACCENT@; background: @HOVER@; }
QCheckBox::indicator:checked { background: @ACCENT@; border-color: @ACCENT@; }
QCheckBox::indicator:disabled { background: @SURFACE_SUBTLE@; border-color: @BORDER@; }

QTableView {
    background: @SURFACE@;
    alternate-background-color: @SURFACE_SUBTLE@;
    gridline-color: @BORDER@;
    selection-background-color: @ACCENT_SOFT@;
    selection-color: @TEXT@;
}
QTableView::item { padding: 6px; border: none; color: @TEXT@; }
QTableView::item:hover { background: @HOVER@; color: @TEXT@; }
QTableView::item:selected, QTableView::item:selected:active, QTableView::item:selected:!active {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
    border: 0;
}
QTableView::item:focus { border: 0; outline: none; }

/* O botão ativo do menu lateral nunca pode virar card branco no tema escuro. */
QPushButton#sideNavButton[active="true"], QPushButton#sideNavButton:checked {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
    border-color: @ACCENT@;
}
QPushButton#sideNavButton[active="true"]:hover, QPushButton#sideNavButton:checked:hover {
    background: @HOVER@;
    color: @TEXT@;
}

/* 3.6.0: regra global anti-beta — todo painel/cartão precisa de respiro e contraste. */
QFrame#dashboardCard, QFrame#dashboardHero, QFrame#dashboardFooter, QFrame#pageCard, QFrame#finalCard,
QFrame#summaryPanel, QFrame#subtlePanel, QFrame#customFieldsCard, QFrame#advancedSettingsPanel,
QFrame#dropZone, QFrame#dropZonePrimary, QFrame#exPanelCard, QFrame#exTemplateRow, QFrame#modelRow, QFrame#signatureRow,
QFrame#trackingRow, QFrame#recipientRow, QFrame#actionBar, QFrame#sendBar, QFrame#identityBar, QFrame#embeddedIntro {
    color: @TEXT@;
}
QLabel#formLabel, QLabel#sendSummary, QLabel#recentTextRow, QLabel#rowArrow, QLabel#exPanelName, QLabel#exPanelSpecs,
QLabel#checkOk, QLabel#checkWarn, QLabel#cardTitle, QLabel#cardTitleSmall, QLabel#dashCardTitle, QLabel#recentTitle,
QLabel#pageTitle, QLabel#pageSubtitle, QLabel#sectionTitle, QLabel#subjectDisplay, QLabel#subjectDisplayEmpty {
    background: transparent;
}
QLabel#formLabel { color: @TEXT@; font-weight: 650; padding: 2px 4px 2px 0px; }
QLabel#sendSummary { color: @TEXT@; font-weight: 750; }
QLabel#recentTextRow, QLabel#exPanelSpecs { color: @MUTED@; }
QLabel#rowArrow { color: @MUTED@; font-weight: 900; }
QLabel#checkOk { color: @SUCCESS@; }
QLabel#checkWarn { color: @WARNING@; }
QFrame#specInputRow { background: transparent; border: 0; }
QStackedWidget#taskStack, QWidget#taskStack { background: transparent; border: 0; }
QFrame#modePage, QWidget#modePage { background: @WINDOW@; color: @TEXT@; border: 0; }
QListWidget#trackingList::item:hover, QListWidget#exPanelList::item:hover, QListWidget#exTemplateList::item:hover {
    background: @HOVER@;
    color: @TEXT@;
}
QListWidget#trackingList::item:selected, QListWidget#exPanelList::item:selected, QListWidget#exTemplateList::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QComboBox {
    padding-left: 10px;
    padding-right: 10px;
}
QTextEdit#smartInput, QPlainTextEdit#smartInput {
    padding: 12px;
}
QToolTip {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 8px 10px;
}

/* 3.7.0: hardening global de estados.
   Corrige estilos antigos mais específicos que deixavam controles brancos no tema escuro
   e garante respiro/contraste em hover, focus, popup e seleção. */
QFrame#topControlWrap {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 14px;
}
QFrame#topControlWrap:hover {
    background: @SURFACE@;
    border-color: @BORDER_STRONG@;
}
QFrame#topControlWrap QLabel#topControlLabel {
    color: @MUTED@;
    background: transparent;
    padding: 0px 2px 3px 2px;
    font-weight: 700;
}
QComboBox#topCombo, QComboBox#topCombo:editable, QFrame#topControlWrap QComboBox#topCombo {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 11px;
    min-height: 42px;
    min-width: 230px;
    padding: 8px 42px 8px 14px;
    font-weight: 700;
    selection-background-color: @ACCENT@;
    selection-color: @SELECTED_TEXT@;
}
QComboBox#topCombo:hover, QComboBox#topCombo:focus, QFrame#topControlWrap QComboBox#topCombo:hover, QFrame#topControlWrap QComboBox#topCombo:focus {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @ACCENT@;
}
QComboBox#topCombo:disabled {
    background: @SURFACE_SUBTLE@;
    color: @MUTED@;
    border-color: @BORDER@;
}
QComboBox#topCombo::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 36px;
    border: 0;
    border-left: 1px solid @BORDER@;
    background: transparent;
    border-top-right-radius: 11px;
    border-bottom-right-radius: 11px;
}
QComboBox#topCombo::drop-down:hover {
    background: @HOVER@;
    border-left-color: @BORDER_STRONG@;
}
QComboBox#topCombo::down-arrow {
    image: none;
    width: 0px;
    height: 0px;
}
QComboBox#topCombo QAbstractItemView, QComboBox#topCombo QListView#comboPopup, QListView#comboPopup, QComboBox QListView#comboPopup {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @ACCENT@;
    border-radius: 12px;
    padding: 6px;
    outline: 0;
    selection-background-color: @ACCENT_SOFT@;
    selection-color: @TEXT@;
}
QComboBox#topCombo QAbstractItemView::item, QListView#comboPopup::item {
    background: transparent;
    color: @TEXT@;
    min-height: 34px;
    padding: 8px 12px;
    border-radius: 8px;
}
QComboBox#topCombo QAbstractItemView::item:hover, QComboBox#topCombo QAbstractItemView::item:selected,
QListView#comboPopup::item:hover, QListView#comboPopup::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QTextBrowser:hover, QComboBox:hover, QTableView:hover, QListWidget:hover, QListView:hover {
    border-color: @BORDER_STRONG@;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTableView:focus, QListWidget:focus, QListView:focus {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @ACCENT@;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QTextBrowser:disabled, QComboBox:disabled, QListView:disabled, QTableView:disabled {
    background: @SURFACE_SUBTLE@;
    color: @MUTED@;
    border-color: @BORDER@;
}
QLineEdit, QTextEdit, QPlainTextEdit {
    placeholder-text-color: @MUTED@;
}
QFrame#dashboardCard:hover, QFrame#pageCard:hover, QFrame#finalCard:hover, QFrame#recipientRow:hover,
QFrame#trackingRow:hover, QFrame#modelRow:hover, QFrame#signatureRow:hover, QFrame#exPanelCard:hover, QFrame#exTemplateRow:hover {
    background: @HOVER@;
    border-color: @BORDER_STRONG@;
}
QFrame#dashboardCard QLabel, QFrame#pageCard QLabel, QFrame#finalCard QLabel, QFrame#recipientRow QLabel,
QFrame#trackingRow QLabel, QFrame#modelRow QLabel, QFrame#signatureRow QLabel, QFrame#exPanelCard QLabel, QFrame#exTemplateRow QLabel {
    background: transparent;
}
QPushButton:hover, QToolButton:hover {
    color: @TEXT@;
}
QPushButton:focus, QToolButton:focus {
    border: 1px solid @ACCENT@;
}
QTableView::item:hover, QTableWidget::item:hover, QListWidget::item:hover, QListView::item:hover {
    background: @HOVER@;
    color: @TEXT@;
}
QTableView::item:selected, QTableWidget::item:selected, QListWidget::item:selected, QListView::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}
QHeaderView::section {
    background: @SURFACE_SUBTLE@;
    color: @TEXT@;
    border: 0;
    border-right: 1px solid @BORDER@;
    border-bottom: 1px solid @BORDER@;
    padding: 9px 10px;
}

/* 4.1.0: correção global final de contraste, side nav, barras e painéis responsivos. */
QPushButton#sideNavButton {
    background: transparent;
    color: @TEXT@;
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 12px 14px;
    text-align: left;
    font-weight: 800;
}
QPushButton#sideNavButton[active="false"], QPushButton#sideNavButton:!checked {
    background: transparent;
    color: @TEXT@;
    border-color: transparent;
}
QPushButton#sideNavButton[active="false"]:hover {
    background: @HOVER@;
    color: @TEXT@;
    border-color: @BORDER@;
}
QPushButton#sideNavButton[active="true"], QPushButton#sideNavButton:checked {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
    border: 1px solid @ACCENT@;
}
QPushButton#sideNavButton[active="true"]:hover, QPushButton#sideNavButton:checked:hover {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
    border-color: @ACCENT@;
}
QFrame#sendBar, QFrame#actionBar {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 16px;
}
QFrame#sendBar QLabel#sendSummary, QFrame#sendBar QLabel {
    color: @TEXT@;
    background: transparent;
}
QFrame#trackingListPanel, QFrame#trackingContextPanel, QFrame#trackingRecipientsPanel,
QFrame#responseCard, QFrame#analysisCard {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 14px;
}
QFrame#trackingContextContent {
    background: transparent;
    color: @TEXT@;
    border: 0;
}
QScrollArea#trackingContextScroll, QScrollArea#trackingContextScroll QWidget#qt_scrollarea_viewport {
    background: transparent;
    border: 0;
}
QListView#trackingList, QListWidget#recipientList {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 12px;
    padding: 6px;
}
QTextBrowser#responseBrowser, QTextBrowser#analysisBrowser {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 12px;
    padding: 10px;
}
QPushButton#secondarySmall, QPushButton#secondaryAction, QPushButton#quietAction {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @BORDER_STRONG@;
}
QPushButton#secondarySmall:hover, QPushButton#secondaryAction:hover, QPushButton#quietAction:hover {
    background: @HOVER@;
    color: @TEXT@;
    border-color: @ACCENT@;
}
QPushButton#accent, QPushButton#primaryAction {
    background: @ACCENT@;
    color: @SELECTED_TEXT@;
    border-color: @ACCENT@;
}
QComboBox QAbstractItemView, QComboBox#topCombo QAbstractItemView, QListView#comboPopup {
    background: @SURFACE@;
    color: @TEXT@;
    border: 1px solid @ACCENT@;
    selection-background-color: @ACCENT_SOFT@;
    selection-color: @TEXT@;
}
QComboBox QAbstractItemView::item, QListView#comboPopup::item {
    background: transparent;
    color: @TEXT@;
    min-height: 34px;
    padding: 8px 12px;
}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected,
QListView#comboPopup::item:hover, QListView#comboPopup::item:selected {
    background: @ACCENT_SOFT@;
    color: @TEXT@;
}


"""
        replacements = {
            "@WINDOW@": p.window,
            "@SURFACE@": p.surface,
            "@SURFACE_SUBTLE@": p.surface_subtle,
            "@INPUT@": p.input_bg,
            "@TEXT@": p.text,
            "@MUTED@": p.text_muted,
            "@ACCENT@": p.accent,
            "@ACCENT_SOFT@": p.accent_soft,
            "@BORDER@": p.border,
            "@BORDER_STRONG@": p.border_strong,
            "@SUCCESS@": p.success,
            "@SUCCESS_BG@": p.success_bg,
            "@WARNING@": p.warning,
            "@WARNING_BG@": p.warning_bg,
            "@DANGER@": p.danger,
            "@DANGER_BG@": p.danger_bg,
            "@HOVER@": p.hover,
            "@SELECTED_TEXT@": p.selected_text,
            "@ACCENT_HOVER@": p.accent,
        }
        for token, value in replacements.items():
            css = css.replace(token, value)
        return css
