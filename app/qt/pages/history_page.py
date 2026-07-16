from __future__ import annotations

import html
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, QUrl, QSettings
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QAbstractItemView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QFileDialog,
    QTextBrowser,
    QSplitter,
    QSizePolicy,
)

from app.application.context import AppContext
from app.core.dashboard_insights import (
    company_label,
    event_title,
    event_type_label,
    human_datetime,
    is_archived,
    recipients,
    response_summary,
    short_text,
    status_group,
    visible_history_rows,
)
from app.core.email_signature import build_html_email_body, first_signature_owner, load_signature_html, resolve_signature_html_path
from app.core.email_templates import dedupe_emails
from app.core.imap_monitor import register_manual_response, sync_inbox_replies
from app.core.response_analyzer import extract_commercial_table, quote_quality_label, split_supplier_reply
from app.core.smtp_handler import get_password_from_profile, send_email_with_profile
from app.qt.icon_utils import get_icon_char, get_icon
from app.qt.ui_scale import scaled_px
from app.qt.models.tracking_list_model import TrackingListModel
from app.qt.delegates.tracking_event_delegate import TrackingEventDelegate


MAX_VISIBLE_ROWS = 80


def _clean(value: Any) -> str:
    return str(value or "").strip()


class _HistoryLoadSignals(QObject):
    done = Signal(str, str, object)  # filter, query, rows
    error = Signal(str)


class _HistoryLoadRunnable(QRunnable):
    def __init__(self, history: Any, current_filter: str, query: str, signals: _HistoryLoadSignals) -> None:
        super().__init__()
        self.history = history
        self.current_filter = current_filter
        self.query = query
        self.signals = signals

    def run(self) -> None:
        try:
            include_archived = self.current_filter == "Arquivadas"
            rows = visible_history_rows(self.history, query=self.query, include_archived=include_archived)
            try:
                self.signals.done.emit(self.current_filter, self.query, rows)
            except RuntimeError:
                pass
        except Exception as exc:  # pragma: no cover - defensive UI path
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass


class _IMAPSyncSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class _IMAPSyncRunnable(QRunnable):
    def __init__(self, config: Any, history: Any, signals: _IMAPSyncSignals, *, max_messages_per_account: int = 120) -> None:
        super().__init__()
        self.config = config
        self.history = history
        self.signals = signals
        self.max_messages_per_account = max_messages_per_account

    def run(self) -> None:
        try:
            summary = sync_inbox_replies(
                self.config,
                self.history,
                max_messages_per_account=self.max_messages_per_account,
            )
            try:
                self.signals.done.emit(summary)
            except RuntimeError:
                pass
        except Exception as exc:  # pragma: no cover - defensive UI path
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass


class IMAPSyncRunnable(_IMAPSyncRunnable):
    """Compatibilidade com o shell antigo: cria seus próprios sinais."""
    def __init__(self, config: Any, history: Any) -> None:
        signals = _IMAPSyncSignals()
        super().__init__(config, history, signals)
        self.signals = signals


def _age_days(ts: object) -> int:
    text = _clean(ts)
    if not text:
        return 0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return max(0, (datetime.now() - dt).days)
    except Exception:
        return 0


class HistoryPage(QWidget):
    """Acompanhar operacional: resposta, pendência e próxima ação sem abrir e-mail."""

    _FILTERS = ("Abertas", "Respondidas", "Arquivadas")

    def __init__(
        self,
        app_context: AppContext,
        *,
        on_status: Callable[[str], None] | None = None,
        on_open_quote: Callable[[dict | None], None] | None = None,
    ) -> None:
        super().__init__()
        self.app_context = app_context
        self._on_status = on_status
        self._on_open_quote = on_open_quote
        self._rows: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._current_filter = "Abertas"
        self._selected_event_id: str | None = None
        self._filter_buttons: dict[str, QPushButton] = {}
        self._is_syncing = False
        self._is_loading = False
        self._last_auto_sync_at = 0.0
        self._auto_sync_interval_sec = 75.0
        self._current_full_email_body = ""
        self._current_supplier_reply_body = ""
        self._current_replies: list[dict] = []
        self._current_reply_index = 0
        self._current_reply_summary: dict = {}
        self._load_seq = 0
        self._history_signals: _HistoryLoadSignals | None = None
        self._imap_signals: _IMAPSyncSignals | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._reload_async)
        self._splitter_settings = QSettings("ComprasVesper", "ComprasVesper")
        self._build_ui()
        QTimer.singleShot(0, self._reload_async)

    def _refresh(self) -> None:
        self._reload_async()

    def on_page_activated(self) -> None:
        # Mostra imediatamente o histórico local e agenda a verificação em segundo plano.
        self._reload_async()
        self._schedule_auto_sync()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._reload_async)
        self._schedule_auto_sync()

    def _schedule_auto_sync(self) -> None:
        QTimer.singleShot(350, self._auto_sync_if_stale)

    def _auto_sync_if_stale(self) -> None:
        if self._is_syncing:
            return
        now = time.monotonic()
        if now - self._last_auto_sync_at < self._auto_sync_interval_sec:
            return
        self._sync_replies_now(auto=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(scaled_px(18), scaled_px(18), scaled_px(18), scaled_px(12))
        root.setSpacing(scaled_px(12))

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Acompanhar")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        subtitle = QLabel("Respostas recebidas, pendências e cobrança sem abrir o e-mail.")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        self.sync_badge = QLabel("Sincronização em segundo plano")
        self.sync_badge.setObjectName("softBadge")
        header.addWidget(self.sync_badge, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        top = QHBoxLayout()
        top.setSpacing(scaled_px(8))
        for name in self._FILTERS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setObjectName("filterChip")
            btn.clicked.connect(lambda _=False, n=name: self._set_filter(n))
            top.addWidget(btn, 0)
            self._filter_buttons[name] = btn
        top.addStretch(1)
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Buscar cotação, fornecedor, frete, OC ou e-mail")
        self.search_edit.textChanged.connect(lambda *_: self._search_timer.start())
        self.search_edit.returnPressed.connect(self._reload_async)
        self.search_edit.setMinimumWidth(scaled_px(380))
        top.addWidget(self.search_edit, 1)
        self.btn_sync = QPushButton("Verificar e-mail")
        self.btn_sync.setObjectName("secondarySmall")
        self.btn_sync.clicked.connect(lambda _=False: self._sync_replies_now(auto=False))
        top.addWidget(self.btn_sync, 0)
        root.addLayout(top)

        self.tracking_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.tracking_splitter.setObjectName("trackingMainSplitter")
        self.tracking_splitter.setChildrenCollapsible(False)
        self.tracking_splitter.setHandleWidth(scaled_px(8))

        left = QFrame(self.tracking_splitter)
        left.setObjectName("dashboardCard")
        left.setMinimumWidth(scaled_px(360))
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(scaled_px(14), scaled_px(14), scaled_px(14), scaled_px(14))
        left_box.setSpacing(scaled_px(10))

        self.left_splitter = QSplitter(Qt.Orientation.Vertical, left)
        self.left_splitter.setObjectName("trackingLeftSplitter")
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(scaled_px(8))

        list_panel = QFrame(self.left_splitter)
        list_panel.setObjectName("trackingListPanel")
        list_panel.setMinimumHeight(scaled_px(120))
        list_box = QVBoxLayout(list_panel)
        list_box.setContentsMargins(scaled_px(10), scaled_px(10), scaled_px(10), scaled_px(10))
        list_box.setSpacing(scaled_px(8))

        list_header = QHBoxLayout()
        list_title = QLabel("Cotações")
        list_title.setObjectName("cardTitleSmall")
        list_header.addWidget(list_title, 0)
        list_header.addStretch(1)
        self.range_label = QLabel("Carregando...")
        self.range_label.setObjectName("muted")
        list_header.addWidget(self.range_label, 0)
        list_box.addLayout(list_header)

        self.list_model = TrackingListModel([], self)
        self.list_widget = QListView(list_panel)
        self.list_widget.setObjectName("trackingList")
        self.list_widget.setModel(self.list_model)
        self.list_widget.setItemDelegate(TrackingEventDelegate(self.list_widget))
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setMinimumHeight(scaled_px(80))
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.list_widget.selectionModel().currentChanged.connect(lambda current, _previous: self._on_list_selection_changed(current.row()))
        list_box.addWidget(self.list_widget, 1)

        # Detalhes e destinatários ficam em painéis próprios para não disputar
        # altura com a lista de cotações. Cada bloco tem scroll interno e o
        # splitter vertical permite o usuário ajustar a altura conforme o uso.
        self.context_panel = QFrame(self.left_splitter)
        self.context_panel.setObjectName("trackingContextPanel")
        self.context_panel.setMinimumHeight(scaled_px(100))
        context_outer = QVBoxLayout(self.context_panel)
        context_outer.setContentsMargins(scaled_px(10), scaled_px(10), scaled_px(10), scaled_px(10))
        context_outer.setSpacing(scaled_px(8))
        details_label = QLabel("Detalhes da cotação")
        details_label.setObjectName("cardTitleSmall")
        context_outer.addWidget(details_label, 0)

        self.context_scroll = QScrollArea(self.context_panel)
        self.context_scroll.setObjectName("trackingContextScroll")
        self.context_scroll.setWidgetResizable(True)
        self.context_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.context_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.context_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        context_content = QFrame(self.context_scroll)
        context_content.setObjectName("trackingContextContent")
        context_box = QVBoxLayout(context_content)
        context_box.setContentsMargins(scaled_px(2), scaled_px(2), scaled_px(2), scaled_px(2))
        context_box.setSpacing(scaled_px(8))

        self.detail_title = QLabel("Selecione uma cotação")
        self.detail_title.setObjectName("dashCardTitle")
        self.detail_title.setWordWrap(True)
        context_box.addWidget(self.detail_title)

        self.detail_status = QLabel("Escolha uma cotação acima para ver quem respondeu e quem falta cobrar.")
        self.detail_status.setObjectName("muted")
        self.detail_status.setWordWrap(True)
        context_box.addWidget(self.detail_status)

        self.summary_box = QFrame(context_content)
        self.summary_box.setObjectName("summaryPanel")
        self.summary_grid = QGridLayout(self.summary_box)
        self.summary_grid.setContentsMargins(scaled_px(10), scaled_px(8), scaled_px(10), scaled_px(8))
        self.summary_grid.setHorizontalSpacing(scaled_px(8))
        self.summary_grid.setVerticalSpacing(scaled_px(8))
        context_box.addWidget(self.summary_box, 0)

        self.next_action = QLabel("Próxima ação: selecione uma cotação.")
        self.next_action.setObjectName("nextAction")
        self.next_action.setWordWrap(True)
        context_box.addWidget(self.next_action)
        context_box.addStretch(1)
        self.context_scroll.setWidget(context_content)
        context_outer.addWidget(self.context_scroll, 1)

        self.recipients_panel = QFrame(self.left_splitter)
        self.recipients_panel.setObjectName("trackingRecipientsPanel")
        self.recipients_panel.setMinimumHeight(scaled_px(90))
        recipients_box = QVBoxLayout(self.recipients_panel)
        recipients_box.setContentsMargins(scaled_px(10), scaled_px(10), scaled_px(10), scaled_px(10))
        recipients_box.setSpacing(scaled_px(8))
        recipients_header = QHBoxLayout()
        recipients_label = QLabel("Destinatários")
        recipients_label.setObjectName("cardTitleSmall")
        recipients_header.addWidget(recipients_label)
        recipients_header.addStretch(1)
        self.recipients_count_label = QLabel("0")
        self.recipients_count_label.setObjectName("muted")
        recipients_header.addWidget(self.recipients_count_label)
        recipients_box.addLayout(recipients_header)

        self.recipients_list = QListWidget(self.recipients_panel)
        self.recipients_list.setObjectName("recipientList")
        self.recipients_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.recipients_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        recipients_box.addWidget(self.recipients_list, 1)

        self.left_splitter.addWidget(list_panel)
        self.left_splitter.addWidget(self.context_panel)
        self.left_splitter.addWidget(self.recipients_panel)
        self.left_splitter.setStretchFactor(0, 4)
        self.left_splitter.setStretchFactor(1, 4)
        self.left_splitter.setStretchFactor(2, 2)
        self.left_splitter.setSizes([scaled_px(180), scaled_px(150), scaled_px(130)])
        left_box.addWidget(self.left_splitter, 1)

        self.detail_panel = QFrame(self.tracking_splitter)
        self.detail_panel.setObjectName("dashboardCard")
        right_box = QVBoxLayout(self.detail_panel)
        right_box.setContentsMargins(scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14))
        right_box.setSpacing(scaled_px(10))

        response_header = QHBoxLayout()
        response_title_box = QVBoxLayout()
        self.response_heading = QLabel("Resposta do fornecedor")
        self.response_heading.setObjectName("dashCardTitle")
        response_title_box.addWidget(self.response_heading)
        self.response_meta = QLabel("Selecione uma cotação para ver a resposta limpa e os dados encontrados.")
        self.response_meta.setObjectName("muted")
        self.response_meta.setWordWrap(True)
        response_title_box.addWidget(self.response_meta)
        response_header.addLayout(response_title_box, 1)
        self.reply_nav_label = QLabel("Resposta 0/0")
        self.reply_nav_label.setObjectName("muted")
        self.reply_nav_label.setToolTip("Use Anterior/Próximo para alternar entre respostas de destinatários diferentes.")
        response_header.addWidget(self.reply_nav_label, 0, Qt.AlignmentFlag.AlignTop)
        self.btn_prev_reply = QPushButton("‹ Anterior")
        self.btn_prev_reply.setObjectName("secondarySmall")
        self.btn_prev_reply.setEnabled(False)
        self.btn_prev_reply.clicked.connect(lambda _=False: self._move_reply(-1))
        response_header.addWidget(self.btn_prev_reply, 0, Qt.AlignmentFlag.AlignTop)
        self.btn_next_reply = QPushButton("Próximo ›")
        self.btn_next_reply.setObjectName("secondarySmall")
        self.btn_next_reply.setEnabled(False)
        self.btn_next_reply.clicked.connect(lambda _=False: self._move_reply(1))
        response_header.addWidget(self.btn_next_reply, 0, Qt.AlignmentFlag.AlignTop)
        self.btn_full_email = QPushButton("Ver e-mail completo")
        self.btn_full_email.setObjectName("secondarySmall")
        self.btn_full_email.setEnabled(False)
        self.btn_full_email.clicked.connect(self._show_full_email_dialog)
        response_header.addWidget(self.btn_full_email, 0, Qt.AlignmentFlag.AlignTop)
        right_box.addLayout(response_header)

        self.response_splitter = QSplitter(Qt.Orientation.Vertical, self.detail_panel)
        self.response_splitter.setObjectName("responseDetailSplitter")
        self.response_splitter.setChildrenCollapsible(False)
        self.response_splitter.setHandleWidth(scaled_px(8))

        response_card = QFrame(self.response_splitter)
        response_card.setObjectName("responseCard")
        response_box = QVBoxLayout(response_card)
        response_box.setContentsMargins(scaled_px(10), scaled_px(10), scaled_px(10), scaled_px(10))
        response_box.setSpacing(scaled_px(8))
        self.response_browser = QTextBrowser(response_card)
        self.response_browser.setObjectName("responseBrowser")
        self.response_browser.setMinimumHeight(scaled_px(260))
        self.response_browser.setOpenExternalLinks(False)
        self.response_browser.anchorClicked.connect(self._on_response_link_clicked)
        response_box.addWidget(self.response_browser, 1)

        analysis_card = QFrame(self.response_splitter)
        analysis_card.setObjectName("analysisCard")
        analysis_box = QVBoxLayout(analysis_card)
        analysis_box.setContentsMargins(scaled_px(10), scaled_px(10), scaled_px(10), scaled_px(10))
        analysis_box.setSpacing(scaled_px(8))
        analysis_title = QLabel("Dados encontrados")
        analysis_title.setObjectName("cardTitleSmall")
        analysis_box.addWidget(analysis_title, 0)
        self.analysis_browser = QTextBrowser(analysis_card)
        self.analysis_browser.setObjectName("analysisBrowser")
        self.analysis_browser.setMinimumHeight(scaled_px(145))
        self.analysis_browser.setOpenExternalLinks(False)
        self.analysis_browser.anchorClicked.connect(self._on_response_link_clicked)
        analysis_box.addWidget(self.analysis_browser, 1)

        self.response_splitter.addWidget(response_card)
        self.response_splitter.addWidget(analysis_card)
        self.response_splitter.setStretchFactor(0, 8)
        self.response_splitter.setStretchFactor(1, 3)
        self.response_splitter.setSizes([scaled_px(520), scaled_px(210)])
        right_box.addWidget(self.response_splitter, 1)

        self.action_bar = QFrame(self.detail_panel)
        self.action_bar.setObjectName("actionBar")
        actions = QHBoxLayout(self.action_bar)
        actions.setContentsMargins(scaled_px(10), scaled_px(7), scaled_px(10), scaled_px(7))
        actions.setSpacing(scaled_px(10))
        self.btn_followup = QPushButton("Aguardar 3 dias")
        self.btn_followup.setObjectName("quietAction")
        self.btn_followup.clicked.connect(self._send_followup_to_pending)
        self.btn_followup.setEnabled(False)
        self.btn_followup.setMinimumWidth(scaled_px(128))
        self.btn_followup.setMaximumWidth(scaled_px(150))
        self.btn_followup.setMinimumHeight(scaled_px(38))
        actions.addWidget(self.btn_followup, 0)
        actions.addStretch(1)
        self.btn_archive = QPushButton("Finalizar cotação")
        self.btn_archive.setObjectName("primaryAction")
        self.btn_archive.clicked.connect(self._toggle_archive_selected)
        self.btn_archive.setEnabled(False)
        self.btn_archive.setMinimumWidth(scaled_px(164))
        self.btn_archive.setMinimumHeight(scaled_px(42))
        actions.addWidget(self.btn_archive, 0)
        right_box.addWidget(self.action_bar, 0)

        self.tracking_splitter.addWidget(left)
        self.tracking_splitter.addWidget(self.detail_panel)
        self.tracking_splitter.setStretchFactor(0, 4)
        self.tracking_splitter.setStretchFactor(1, 7)
        self.tracking_splitter.setSizes([scaled_px(470), scaled_px(820)])
        self.tracking_splitter.splitterMoved.connect(lambda *_: self._save_splitter_states())
        self.left_splitter.splitterMoved.connect(lambda *_: self._save_splitter_states())
        self.response_splitter.splitterMoved.connect(lambda *_: self._save_splitter_states())
        root.addWidget(self.tracking_splitter, 1)
        QTimer.singleShot(0, self._restore_splitter_states)
        self._update_filter_buttons()
        self._render_detail(None)

    def _restore_splitter_states(self) -> None:
        for key, splitter in (
            ("history/main_splitter", self.tracking_splitter),
            ("history/left_splitter", self.left_splitter),
            ("history/response_splitter", self.response_splitter),
        ):
            try:
                state = self._splitter_settings.value(key)
                if state:
                    splitter.restoreState(state)
            except Exception:
                pass
        QTimer.singleShot(0, self._normalize_splitter_sizes)

    def _normalize_splitter_sizes(self) -> None:
        """Evita estados salvos que deixam painéis espremidos/bugados visualmente."""
        try:
            self._enforce_splitter_minimums(self.left_splitter, [scaled_px(140), scaled_px(110), scaled_px(100)])
            self._enforce_splitter_minimums(self.response_splitter, [scaled_px(180), scaled_px(100)])
            self._enforce_splitter_minimums(self.tracking_splitter, [scaled_px(280), scaled_px(380)])
        except Exception:
            pass

    def _enforce_splitter_minimums(self, splitter: QSplitter, minimums: list[int]) -> None:
        sizes = splitter.sizes()
        if len(sizes) != len(minimums):
            return
        changed = False
        adjusted = []
        for size, minimum in zip(sizes, minimums):
            value = max(int(size), int(minimum))
            adjusted.append(value)
            changed = changed or value != int(size)
        if changed:
            splitter.setSizes(adjusted)

    def _save_splitter_states(self) -> None:
        try:
            self._splitter_settings.setValue("history/main_splitter", self.tracking_splitter.saveState())
            self._splitter_settings.setValue("history/left_splitter", self.left_splitter.saveState())
            self._splitter_settings.setValue("history/response_splitter", self.response_splitter.saveState())
        except Exception:
            pass

    def _set_filter(self, name: str) -> None:
        self._current_filter = name
        self._update_filter_buttons()
        self._reload_async()

    def _update_filter_buttons(self) -> None:
        for name, btn in self._filter_buttons.items():
            btn.setChecked(name == self._current_filter)

    def _reload_async(self) -> None:
        if self._is_loading:
            # Deixa o último filtro/busca ganhar ao final do worker atual.
            self._pending_reload = True
            return
        self._pending_reload = False
        self._is_loading = True
        self._load_seq += 1
        current_filter = self._current_filter
        query = _clean(self.search_edit.text())
        self.range_label.setText("Atualizando...")
        self._history_signals = _HistoryLoadSignals()
        self._history_signals.done.connect(self._on_history_loaded)
        self._history_signals.error.connect(self._on_history_error)
        QThreadPool.globalInstance().start(_HistoryLoadRunnable(self.app_context.state.history, current_filter, query, self._history_signals))

    def _on_history_loaded(self, current_filter: str, query: str, rows_obj: object) -> None:
        self._is_loading = False
        if current_filter != self._current_filter or query != _clean(self.search_edit.text()):
            self._reload_async()
            return
        rows = [r for r in list(rows_obj or []) if isinstance(r, dict)]
        self._rows = rows
        filtered = self._apply_filter(rows)
        self._filtered_rows = filtered
        self._populate_list(filtered)
        shown = min(len(filtered), MAX_VISIBLE_ROWS)
        suffix = "" if len(filtered) <= MAX_VISIBLE_ROWS else f" — mostrando {shown} mais recentes"
        self.range_label.setText(f"{len(filtered)} cotação(ões){suffix}")
        self._set_status("Pronto")
        if self._pending_reload:
            self._reload_async()

    def _on_history_error(self, message: str) -> None:
        self._is_loading = False
        self.range_label.setText("Erro ao carregar")
        self._set_status("Histórico indisponível")
        if not self._filtered_rows:
            self._render_detail(None)

    def _apply_filter(self, rows: list[dict]) -> list[dict]:
        if self._current_filter == "Arquivadas":
            return rows
        if self._current_filter == "Respondidas":
            return [row for row in rows if status_group(row) in {"Respondido", "Confirmado", "Sem cotação válida"}]
        return [row for row in rows if status_group(row) in {"Pendente", "Parcial", "Falha"}]

    def _populate_list(self, rows: list[dict]) -> None:
        previous_id = self._selected_event_id
        display_rows = rows[:MAX_VISIBLE_ROWS]
        selected_index = 0
        for i, row in enumerate(display_rows):
            if previous_id and _clean(row.get("event_id")) == previous_id:
                selected_index = i
        self.list_model.set_rows(display_rows)
        if display_rows:
            idx = self.list_model.index(min(selected_index, len(display_rows) - 1), 0)
            self.list_widget.setCurrentIndex(idx)
            self._render_detail(display_rows[min(selected_index, len(display_rows) - 1)])
        else:
            self._render_detail(None)

    def _row_icon_key(self, row: dict) -> str:
        kind = event_type_label(row).casefold()
        if "frete" in kind:
            return "freight"
        if "ordem" in kind:
            return "purchase_order"
        if "pain" in kind:
            return "ex_panels"
        return "material"

    def _row_widget(self, row: dict) -> QWidget:
        frame = QFrame(self.list_widget)
        frame.setObjectName("trackingRow")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(scaled_px(12), scaled_px(8), scaled_px(10), scaled_px(8))
        layout.setSpacing(scaled_px(10))
        icon_key = self._row_icon_key(row)
        icon = QLabel()
        icon.setObjectName("recentIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            qicon = get_icon(icon_key, color="#075C91", scale_factor=1.0)
            if qicon and not qicon.isNull():
                icon.setPixmap(qicon.pixmap(scaled_px(21), scaled_px(21)))
            else:
                icon.setText(get_icon_char(icon_key))
        except Exception:
            icon.setText(get_icon_char(icon_key))
        layout.addWidget(icon, 0)

        texts = QVBoxLayout()
        texts.setSpacing(scaled_px(4))
        title = QLabel(event_title(row))
        title.setObjectName("recentTitle")
        title.setWordWrap(False)
        title.setToolTip(event_title(row))
        texts.addWidget(title)
        summary = response_summary(row, self._rows)
        answered = int(summary.get("answered_count") or 0)
        total = int(summary.get("total") or len(recipients(row)) or 0)
        meta = QLabel(f"{event_type_label(row)} • {human_datetime(row.get('ts'))} • {answered}/{total} responderam")
        meta.setObjectName("muted")
        meta.setWordWrap(False)
        texts.addWidget(meta)
        layout.addLayout(texts, 1)

        status = status_group(row)
        pill = QLabel(status)
        pill.setObjectName("statusError" if status == "Falha" else "statusOk" if status in {"Respondido", "Confirmado"} else "statusInfo" if status == "Sem cotação válida" else "statusWarn")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setMinimumWidth(scaled_px(76))
        layout.addWidget(pill, 0)
        arrow = QLabel("›")
        arrow.setObjectName("rowArrow")
        layout.addWidget(arrow, 0)
        return frame

    def _on_list_selection_changed(self, row_index: int) -> None:
        row = self.list_model.row_at(row_index) if hasattr(self, "list_model") else None
        self._render_detail(row if isinstance(row, dict) else None)

    def _selected_event(self) -> dict | None:
        idx = self.list_widget.currentIndex()
        row = self.list_model.row_at(idx.row()) if idx.isValid() and hasattr(self, "list_model") else None
        return row if isinstance(row, dict) else None

    def _clear_summary_grid(self) -> None:
        while self.summary_grid.count():
            item = self.summary_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _summary_pair(self, label: str, value: str) -> QFrame:
        frame = QFrame(self.summary_box)
        frame.setObjectName("summaryCell")
        box = QVBoxLayout(frame)
        # 3.5.0: o card circular/bordado precisa de respiro interno.
        # Margem zero deixava texto colado na borda, passando sensação de beta.
        box.setContentsMargins(scaled_px(12), scaled_px(8), scaled_px(12), scaled_px(8))
        box.setSpacing(scaled_px(3))
        frame.setMinimumHeight(scaled_px(58))
        lab = QLabel(label)
        lab.setObjectName("muted")
        val = QLabel(value or "-")
        val.setObjectName("summaryValue")
        val.setWordWrap(True)
        box.addWidget(lab)
        box.addWidget(val)
        return frame

    def _render_detail(self, row: dict | None) -> None:
        self._clear_summary_grid()
        self.recipients_list.clear()
        previous_event_id = self._selected_event_id
        self._selected_event_id = _clean(row.get("event_id")) if row else None
        if self._selected_event_id != previous_event_id:
            self._current_reply_index = 0
        enabled = bool(row)
        self.btn_archive.setEnabled(enabled)
        self.recipients_count_label.setText("0 destinatário(s)")
        if not row:
            self.btn_followup.setEnabled(False)
            self.detail_title.setText("Selecione uma cotação")
            self.detail_status.setText("As respostas salvas aparecem aqui. A verificação do e-mail roda em segundo plano.")
            self.next_action.setText("Próxima ação: escolha uma cotação aberta ou busque pelo fornecedor.")
            c = self._browser_html_colors()
            self.response_heading.setText("Resposta do fornecedor")
            self.response_meta.setText("Selecione uma cotação para ver a resposta limpa e os dados encontrados.")
            self._current_full_email_body = ""
            self._current_supplier_reply_body = ""
            self.btn_full_email.setEnabled(False)
            self._current_replies = []
            self._current_reply_summary = {}
            self._update_reply_nav()
            self.response_browser.setHtml(
                f"<html><body style='margin:0;background:{c['bg']};color:{c['muted']};font-family:Segoe UI,Arial,sans-serif'>"
                "<p>Nenhuma cotação selecionada.</p></body></html>"
            )
            self.analysis_browser.setHtml(
                f"<html><body style='margin:0;background:{c['bg']};color:{c['muted']};font-family:Segoe UI,Arial,sans-serif'>"
                "<p>Os dados encontrados aparecerão aqui.</p></body></html>"
            )
            return

        status = status_group(row)
        recs = recipients(row)
        self.recipients_count_label.setText(f"{len(recs)} destinatário(s)")
        summary = response_summary(row, self._rows)
        replies = list(summary.get("replies") or [])
        archived = is_archived(row)
        self.btn_archive.setText("Reativar" if archived else "Finalizar cotação")
        self.detail_title.setText(event_title(row))
        answered_count = int(summary.get("answered_count") or 0)
        pending_count = int(summary.get("pending_count") or 0)
        total = len(recs)
        followup_due = _age_days(row.get("ts")) >= 3
        self.btn_followup.setText("Cobrar pendentes" if followup_due else "Aguardar 3 dias")
        self.btn_followup.setEnabled(pending_count > 0 and not archived and followup_due)
        self.detail_status.setText(f"{event_type_label(row)} • {company_label(row)} • {human_datetime(row.get('ts'))} • {answered_count}/{total} respondeu • {status}")
        self.summary_grid.addWidget(self._summary_pair("Empresa", company_label(row)), 0, 0)
        self.summary_grid.addWidget(self._summary_pair("Destinatários", str(total)), 0, 1)
        self.summary_grid.addWidget(self._summary_pair("Respondidos", f"{answered_count}/{total}"), 0, 2)
        self.summary_grid.addWidget(self._summary_pair("Assunto", short_text(row.get("subject"), 72)), 1, 0, 1, 3)

        if archived:
            self.next_action.setText("Arquivada: reative apenas se precisar voltar a acompanhar esta cotação.")
        elif status == "Falha":
            self.next_action.setText("Próxima ação: revisar envio ou reenviar para quem falhou.")
        elif pending_count > 0 and answered_count > 0:
            self.next_action.setText("Próxima ação: ler resposta recebida e cobrar somente quem ainda falta.")
        elif pending_count > 0:
            dias = _age_days(row.get("ts"))
            self.next_action.setText("Próxima ação: cobrar pendentes." if dias >= 3 else f"Próxima ação: aguardar. Cobrança sugerida após 3 dias sem resposta ({dias}/3).")
        elif replies:
            self.next_action.setText("Próxima ação: conferir os dados encontrados e finalizar a cotação.")
        else:
            self.next_action.setText("Próxima ação: aguardar resposta.")

        self._render_response_preview(row, replies, summary)
        answered_emails = {str(r.get("email", "")).casefold() for r in summary.get("answered", [])}
        for recipient in recs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, dict(recipient))
            item.setSizeHint(QSize(10, scaled_px(48)))
            self.recipients_list.addItem(item)
            self.recipients_list.setItemWidget(item, self._recipient_row(recipient, str(recipient.get("email", "")).casefold() in answered_emails))

    def _browser_html_colors(self) -> dict[str, str]:
        # QTextBrowser nem sempre herda todas as cores do QSS dentro do HTML.
        # Detectamos claro/escuro pela paleta e aplicamos cores explícitas com
        # contraste AA, evitando texto quase invisível em painéis escuros.
        # A paleta do QTextBrowser nem sempre reflete o QSS aplicado ao Base.
        # Usar a Window palette global evita HTML claro dentro de painel escuro
        # e texto escuro quase invisível no Acompanhar.
        app = QApplication.instance()
        bg = (app.palette().color(QPalette.ColorRole.Window) if app is not None else self.palette().color(QPalette.ColorRole.Window))
        dark = bg.lightness() < 128
        if dark:
            return {
                "bg": "#101927",
                "surface": "#172438",
                "surface2": "#1C2D44",
                "text": "#F7FAFC",
                "muted": "#C4D0DF",
                "border": "#36506E",
                "accent": "#5AA7FF",
            }
        return {
            "bg": "#FFFFFF",
            "surface": "#F8FBFF",
            "surface2": "#EEF5FF",
            "text": "#10213A",
            "muted": "#5A6B84",
            "border": "#D7E1EE",
            "accent": "#0F5DA8",
        }

    def _render_response_preview(self, row: dict, replies: list[dict], summary: dict) -> None:
        c = self._browser_html_colors()

        def html_doc(body: str, *, compact: bool = False) -> str:
            padding = "0" if compact else "2px"
            return (
                "<html><head><style>"
                f"body{{margin:0;padding:{padding};background:{c['bg']};color:{c['text']};font-family:Segoe UI,Arial,sans-serif;font-size:14px;}}"
                f"h2{{margin:0 0 10px 0;color:{c['text']};font-weight:800;}}"
                f"h3{{margin:10px 0 8px 0;color:{c['text']};font-weight:800;}}"
                f"p,li,td,th,div,span,b{{color:{c['text']};}}"
                f".meta{{background:{c['surface']};border:1px solid {c['border']};border-radius:10px;padding:9px 12px;line-height:1.45;}}"
                f".reply-box{{background:{c['surface2']};border:1px solid {c['border']};border-radius:12px;padding:16px 18px;line-height:1.62;font-size:14px;}}"
                f".quoted-note{{margin-top:12px;background:{c['surface']};border:1px dashed {c['border']};border-radius:10px;padding:10px 12px;color:{c['muted']};line-height:1.45;}}"
                f".muted{{color:{c['muted']};}} a{{color:{c['accent']};}}"
                f"table.data-table{{border-collapse:collapse;background:{c['surface']};border:1px solid {c['border']};border-radius:8px;}}"
                f"table.data-table th{{background:{c['surface2']};color:{c['text']};font-weight:700;border-bottom:1px solid {c['border']};padding:8px;}}"
                f"table.data-table td{{background:{c['surface']};color:{c['text']};border-bottom:1px solid {c['border']};padding:8px;}}"
                f".price-card{{background:{c['surface']};border:1px solid {c['border']};border-radius:10px;padding:9px 11px;margin:0 0 8px 0;}}"
                "</style></head>"
                f"<body style='background:{c['bg']};color:{c['text']}'>" + body + "</body></html>"
            )

        self._current_replies = list(replies or [])
        self._current_reply_summary = dict(summary or {})
        if not replies:
            self._update_reply_nav()
            self.response_heading.setText("Resposta do fornecedor")
            self.response_meta.setText("Ainda sem resposta recebida para esta cotação.")
            self._current_full_email_body = ""
            self._current_supplier_reply_body = ""
            self.btn_full_email.setEnabled(False)
            self._current_replies = []
            self._current_reply_summary = {}
            self._update_reply_nav()
            self.response_browser.setHtml(html_doc(
                "<div class='reply-box'><h2>Ainda sem resposta</h2>"
                "<p>Quando algum fornecedor responder, a resposta limpa aparecerá aqui com prioridade de leitura.</p></div>"
            ))
            pending = summary.get("pending") or []
            items = "".join(
                f"<li><b>{html.escape(short_text(r.get('empresa') or r.get('email'), 60))}</b> — {html.escape(short_text(r.get('email'), 54))}</li>"
                for r in pending[:10]
                if isinstance(r, dict)
            )
            self.analysis_browser.setHtml(html_doc(
                "<p><b>Dados encontrados automaticamente</b></p>"
                "<p class='muted'>A extração aparecerá aqui assim que uma resposta válida chegar.</p>"
                f"<p><b>Pendentes:</b></p><ul>{items or '<li>Nenhum destinatário pendente.</li>'}</ul>",
                compact=True,
            ))
            return

        self._current_reply_index = max(0, min(self._current_reply_index, len(replies) - 1))
        self._update_reply_nav()
        reply = replies[self._current_reply_index]
        extra = reply.get("extra") or {}
        sender = _clean(extra.get("sender_name") if isinstance(extra, dict) else "") or _clean(extra.get("sender") if isinstance(extra, dict) else "")
        sender_email = _clean(extra.get("sender") if isinstance(extra, dict) else "")
        full_body = _clean(reply.get("body")) or "Sem corpo disponível."
        answer_body, quoted_history = split_supplier_reply(full_body)
        answer_body = answer_body or full_body
        self._current_full_email_body = full_body
        self._current_supplier_reply_body = answer_body
        self.btn_full_email.setEnabled(True)
        rows = extract_commercial_table(answer_body)
        quality = quote_quality_label(answer_body)
        answer_html = html.escape(answer_body[:12000]).replace("\n", "<br>")
        quoted_lines = len([line for line in quoted_history.splitlines() if line.strip()]) if quoted_history else 0
        when = human_datetime(reply.get("ts"))
        self.response_heading.setText("Resposta do fornecedor")
        meta_parts = [sender or "Fornecedor", sender_email, when, quality]
        self.response_meta.setText(" • ".join(html.escape(p) for p in meta_parts if p))
        self.response_browser.setHtml(html_doc(
            f"<div class='reply-box'>{answer_html}</div>"
            + (
                f"<div class='quoted-note'>Histórico citado ocultado da visualização principal: {quoted_lines} linha(s) do e-mail original. "
                "Use o original completo apenas se precisar auditar a conversa.</div>"
                if quoted_lines else ""
            )
        ))

        if rows:
            data_rows = "".join(
                "<tr>"
                f"<td>{html.escape(r.get('item',''))}</td>"
                f"<td>{html.escape(r.get('preco',''))}</td>"
                f"<td>{html.escape(r.get('prazo','') or 'não informado')}</td>"
                f"<td>{html.escape(r.get('pagamento','') or 'não informado')}</td>"
                "</tr>"
                for r in rows
            )
            table_html = (
                "<table class='data-table' cellspacing='0' cellpadding='6' width='100%'>"
                "<tr><th align='left'>Item</th><th align='left'>Preço</th><th align='left'>Prazo</th><th align='left'>Pagamento</th></tr>"
                f"{data_rows}</table>"
                "<p class='muted'>Confira os valores detectados com a resposta antes de finalizar.</p>"
            )
        else:
            table_html = "<p><b>Sem cotação válida detectada.</b></p><p class='muted'>O fornecedor respondeu, mas não localizei preço, prazo ou condição de pagamento.</p>"

        attach_html = "<p class='muted'>Anexos: nenhum recebido.</p>"
        attachments = extra.get("attachments") if isinstance(extra, dict) else []
        if isinstance(attachments, list) and attachments:
            lines = []
            for i, att in enumerate(attachments, 1):
                if not isinstance(att, dict):
                    continue
                filename = html.escape(_clean(att.get("filename")) or f"anexo_{i}")
                path = _clean(att.get("path"))
                size = int(att.get("size_bytes") or 0)
                size_text = f"{size/1024:.1f} KB" if size < 1024 * 1024 else f"{size/1024/1024:.1f} MB"
                if path:
                    lines.append(f"<li><b>{filename}</b> <span class='muted'>({size_text})</span> — <a href='open://{html.escape(path)}'>Abrir</a> · <a href='save://{html.escape(path)}'>Salvar como</a></li>")
                else:
                    lines.append(f"<li><b>{filename}</b> <span class='muted'>({size_text})</span></li>")
            attach_html = "<p><b>Anexos recebidos</b></p><ul>" + "".join(lines) + "</ul>" if lines else attach_html

        self.analysis_browser.setHtml(html_doc(
            table_html + attach_html,
            compact=True,
        ))

    def _update_reply_nav(self) -> None:
        total_replies = len(getattr(self, "_current_replies", []) or [])
        total_recipients = int((getattr(self, "_current_reply_summary", {}) or {}).get("total") or 0)
        if total_replies <= 0:
            self.reply_nav_label.setText("Resposta 0/0")
            self.btn_prev_reply.setEnabled(False)
            self.btn_next_reply.setEnabled(False)
            return
        current = max(0, min(self._current_reply_index, total_replies - 1)) + 1
        if total_recipients:
            self.reply_nav_label.setText(f"Resposta {current}/{total_replies} • {total_recipients} destinatário(s)")
        else:
            self.reply_nav_label.setText(f"Resposta {current}/{total_replies}")
        self.btn_prev_reply.setEnabled(current > 1)
        self.btn_next_reply.setEnabled(current < total_replies)

    def _move_reply(self, delta: int) -> None:
        if not getattr(self, "_current_replies", None):
            return
        self._current_reply_index = max(0, min(self._current_reply_index + int(delta), len(self._current_replies) - 1))
        row = self._selected_event()
        if not row:
            return
        summary = response_summary(row, self._rows)
        replies = list(summary.get("replies") or [])
        self._render_response_preview(row, replies, summary)

    def _show_full_email_dialog(self) -> None:
        body = self._current_full_email_body or self._current_supplier_reply_body
        if not body:
            QMessageBox.information(self, "E-mail completo", "Nenhum e-mail completo disponível para esta cotação.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("E-mail completo recebido")
        dlg.resize(scaled_px(860), scaled_px(620))
        box = QVBoxLayout(dlg)
        box.setContentsMargins(scaled_px(16), scaled_px(16), scaled_px(16), scaled_px(16))
        box.setSpacing(scaled_px(10))
        title = QLabel("E-mail completo")
        title.setObjectName("dashCardTitle")
        box.addWidget(title, 0)
        info = QLabel("Use esta visualização apenas para auditoria. A tela principal mostra a resposta limpa do fornecedor.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        box.addWidget(info, 0)
        browser = QTextBrowser(dlg)
        browser.setObjectName("responseBrowser")
        c = self._browser_html_colors()
        body_html = html.escape(body[:50000]).replace("\n", "<br>")
        browser.setHtml(
            f"<html><body style='margin:0;background:{c['bg']};color:{c['text']};"
            "font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.55;'>"
            f"<div style='background:{c['surface']};border:1px solid {c['border']};border-radius:12px;"
            f"padding:14px 16px;color:{c['text']};'>{body_html}</div></body></html>"
        )
        box.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
        buttons.rejected.connect(dlg.reject)
        box.addWidget(buttons, 0)
        dlg.exec()

    def _on_response_link_clicked(self, url: QUrl) -> None:
        scheme = url.scheme().lower()
        path = url.toString()[len(scheme) + 3:] if scheme in {"open", "save"} else url.toLocalFile()
        path = path.replace("%20", " ")
        if not path:
            return
        if scheme == "open":
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        if scheme == "save":
            src = Path(path)
            target, _ = QFileDialog.getSaveFileName(self, "Salvar anexo como", src.name)
            if target:
                try:
                    shutil.copy2(src, target)
                    self._set_status("Anexo salvo.")
                except Exception:
                    QMessageBox.warning(self, "Anexo", "Não foi possível salvar o anexo.")
            return

    def _recipient_row(self, recipient: dict[str, str], answered: bool = False) -> QWidget:
        frame = QFrame(self.recipients_list)
        frame.setObjectName("recipientRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(scaled_px(8), scaled_px(4), scaled_px(8), scaled_px(4))
        name = QLabel(short_text(recipient.get("empresa"), 34))
        name.setObjectName("recipientName")
        row.addWidget(name, 1)
        email = QLabel(short_text(recipient.get("email"), 40))
        email.setObjectName("recipientEmail")
        row.addWidget(email, 1)
        status = QLabel("OK" if answered else "Pendente")
        status.setObjectName("statusOk" if answered else "statusWarn")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(status, 0)
        return frame

    def _sync_replies_now(self, *, auto: bool = False) -> None:
        if self._is_syncing:
            return
        self._is_syncing = True
        self._last_auto_sync_at = time.monotonic()
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("Atualizando..." if auto else "Verificando...")
        self.sync_badge.setText("Verificando respostas em segundo plano...")
        self._imap_signals = _IMAPSyncSignals()
        self._imap_signals.done.connect(self._on_sync_done)
        self._imap_signals.error.connect(self._on_sync_error)
        max_messages = 80 if auto else 160
        QThreadPool.globalInstance().start(
            _IMAPSyncRunnable(
                self.app_context.state.config,
                self.app_context.state.history,
                self._imap_signals,
                max_messages_per_account=max_messages,
            )
        )

    def _on_sync_done(self, summary) -> None:
        self._is_syncing = False
        self.btn_sync.setEnabled(True)
        self.btn_sync.setText("Verificar e-mail")
        self.sync_badge.setText(summary.message() if hasattr(summary, "message") else "E-mail verificado")
        self._reload_async()

    def _on_sync_error(self, err_msg: str) -> None:
        self._is_syncing = False
        self.btn_sync.setEnabled(True)
        self.btn_sync.setText("Verificar e-mail")
        self.sync_badge.setText("Não foi possível verificar e-mail")
        self._set_status("Não foi possível verificar respostas. Veja Configurações se continuar.")

    def _pending_recipients_for_selected(self) -> list[dict[str, str]]:
        row = self._selected_event()
        if not row:
            return []
        summary = response_summary(row, self._rows)
        pending = summary.get("pending")
        if isinstance(pending, list):
            return [r for r in pending if isinstance(r, dict) and _clean(r.get("email"))]
        return [r for r in recipients(row) if _clean(r.get("email"))]

    def _copy_recipients(self) -> None:
        row = self._selected_event()
        emails = [r.get("email", "") for r in recipients(row or {}) if _clean(r.get("email"))]
        if not emails:
            QMessageBox.information(self, "Destinatários", "Essa cotação não tem destinatários para copiar.")
            return
        QGuiApplication.clipboard().setText("; ".join(emails))
        self._set_status(f"{len(emails)} e-mail(s) copiados")

    def _followup_body(self, row: dict) -> str:
        return (
            "Prezados,\n\n"
            "Poderiam, por gentileza, nos retornar sobre a cotação abaixo?\n\n"
            f"{event_title(row)}\n"
            f"Assunto: {_clean(row.get('subject'))}\n\n"
            "Fico no aguardo."
        )

    def _send_followup_to_pending(self) -> None:
        row = self._selected_event()
        if not row:
            return
        pending = self._pending_recipients_for_selected()
        if not pending:
            QMessageBox.information(self, "Cobrar pendentes", "Não há destinatários pendentes nesta cotação.")
            return
        emails = dedupe_emails(r.get("email", "") for r in pending)
        if not emails:
            QMessageBox.information(self, "Cobrar pendentes", "Não encontrei e-mails pendentes válidos.")
            return
        if QMessageBox.question(
            self,
            "Cobrar pendentes",
            f"Enviar cobrança para {len(emails)} destinatário(s) pendente(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        cfg = self.app_context.state.config
        profile = cfg.get_active_profile()
        if profile is None:
            QMessageBox.warning(self, "Cobrar pendentes", "Conta de envio não configurada.")
            return
        password = get_password_from_profile(profile, allow_prompt=True)
        if not password:
            QMessageBox.warning(self, "Cobrar pendentes", "Senha do e-mail de envio não informada.")
            return
        subject = "Retorno pendente — " + _clean(row.get("subject"))
        body = self._followup_body(row)
        signature_html = ""
        try:
            owner = first_signature_owner(cfg) or ""
            active_key = str(getattr(cfg, "smtp_active_profile", "vesper") or "vesper")
            sig_path = resolve_signature_html_path(owner, active_key, str(getattr(profile, "label", "") or "")) if owner else ""
            signature_html = load_signature_html(sig_path) if sig_path else ""
        except Exception:
            signature_html = ""
        result = send_email_with_profile(cfg, emails, subject, body, body_html=build_html_email_body(body, signature_html), password=password, include_profile_bcc=True)
        if result.success:
            history = self.app_context.state.history
            if history is not None:
                for rec in pending:
                    history.record_send_event(
                        status="sent_smtp_ok",
                        product_query=_clean(row.get("product_query")),
                        subject=subject,
                        body=body,
                        recipients=[rec],
                        items=[],
                        failed_emails=list(result.failed_emails or []),
                        event_type="followup_sent",
                        extra={
                            "source_event_id": _clean(row.get("event_id")),
                            "rfq_id": _clean((row.get("extra") or {}).get("rfq_id") if isinstance(row.get("extra"), dict) else ""),
                            "recipient_email": _clean(rec.get("email")),
                            "stage": 1,
                        },
                    )
            QMessageBox.information(self, "Cobrar pendentes", result.message or "Cobrança enviada.")
        else:
            QMessageBox.warning(self, "Cobrar pendentes", result.message or "Falha ao enviar cobrança.")
        self._reload_async()

    def _register_response_manually(self) -> None:
        row = self._selected_event()
        if not row:
            return
        pending = self._pending_recipients_for_selected() or recipients(row)
        if not pending:
            QMessageBox.information(self, "Registrar resposta", "Esta cotação não tem destinatários.")
            return
        labels = [f"{_clean(r.get('empresa')) or _clean(r.get('email'))} — {_clean(r.get('email'))}" for r in pending]
        choice, ok = QInputDialog.getItem(self, "Registrar resposta", "Fornecedor que respondeu:", labels, 0, False)
        if not ok or not choice:
            return
        idx = labels.index(choice) if choice in labels else 0
        recipient = pending[idx]
        text, ok = QInputDialog.getMultiLineText(self, "Registrar resposta", "Cole ou escreva a resposta recebida:", "")
        if not ok:
            return
        ok_save, msg = register_manual_response(
            self.app_context.state.history,
            row,
            supplier_name=_clean(recipient.get("empresa")),
            supplier_email=_clean(recipient.get("email")),
            response_text=text or "Resposta registrada manualmente.",
        )
        if ok_save:
            QMessageBox.information(self, "Registrar resposta", "Resposta registrada com sucesso.")
        else:
            QMessageBox.warning(self, "Registrar resposta", msg)
        self._reload_async()

    def _toggle_archive_selected(self) -> None:
        row = self._selected_event()
        if not row:
            return
        history = self.app_context.state.history
        if history is None or not hasattr(history, "set_event_archived"):
            QMessageBox.warning(self, "Arquivar", "Histórico não está pronto para arquivar esta cotação.")
            return
        make_archived = not is_archived(row)
        if make_archived:
            if QMessageBox.question(
                self,
                "Finalizar cotação",
                "Finalizar esta cotação e remover da rotina diária? Ela continuará em Arquivadas.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        ok, msg, _updated = history.set_event_archived(
            event_id=_clean(row.get("event_id")),
            archived=make_archived,
            actor=_clean(getattr(history, "user", "")),
        )
        if not ok:
            QMessageBox.warning(self, "Arquivar", msg or "Não foi possível alterar o arquivo.")
            return
        self._set_status("Cotação finalizada." if make_archived else "Cotação reativada.")
        self._reload_async()

    def _open_quote(self) -> None:
        row = self._selected_event()
        if not row:
            QMessageBox.warning(self, "Atenção", "Selecione uma cotação primeiro.")
            return
        if self._on_open_quote:
            self._on_open_quote(dict(row))

    def _set_status(self, text: str) -> None:
        if self._on_status:
            self._on_status(text)
