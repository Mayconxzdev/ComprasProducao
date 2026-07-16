from __future__ import annotations

import os
import webbrowser
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QRunnable, QSettings, QTimer, Qt, QThreadPool, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.application.context import AppContext
from app.core.bootstrap_runtime import ensure_runtime_bootstrap
from app.core.utils_text import normalize_text
from app.qt.delegates import SupplierCardDelegate, SupplierProductsDelegate, SupplierSelectDelegate
from app.qt.models import SupplierColumns, SupplierFilterProxyModel, SupplierTableModel
from app.qt.services import SupplierEditService
from app.qt.ui_scale import scaled_window_size
from app.qt.widgets import PageStateStack, SmartSuggestLineEdit, SuggestionOption, resolve_suggestions

from .admin_audit_dialog import AdminAuditDialog


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _supplier_text(supplier: object, key: str) -> str:
    if isinstance(supplier, dict):
        return _clean(supplier.get(key))
    return _clean(getattr(supplier, key, ""))


def supplier_context_options(suppliers: list[object]) -> list[SuggestionOption]:
    options: list[SuggestionOption] = []
    seen: set[str] = set()
    for supplier in suppliers:
        for text in (
            _supplier_text(supplier, "empresa"),
            _supplier_text(supplier, "contato_nome") or _supplier_text(supplier, "contato"),
            _supplier_text(supplier, "telefone"),
            _supplier_text(supplier, "email"),
        ):
            norm = normalize_text(text)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            options.append(SuggestionOption(label=text, value=text))
    return options


class _ReloadSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class _ReloadRunnable(QRunnable):
    def __init__(
        self,
        *,
        app_context: AppContext,
        force_refresh: bool,
        signals: _ReloadSignals,
    ) -> None:
        super().__init__()
        self.app_context = app_context
        self.force_refresh = force_refresh
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.app_context.reload_base_uc.execute(force_refresh=self.force_refresh)
            try:
                self.signals.done.emit(result)
            except RuntimeError:
                # Receiver was deleted while worker was running.
                pass
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass


class _NewSupplierDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Novo fornecedor")
        self.resize(*scaled_window_size(460, 260, min_width=420, min_height=240))
        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.company = QLineEdit(self)
        self.email = QLineEdit(self)
        self.contact = QLineEdit(self)
        self.phone = QLineEdit(self)
        self.products = QLineEdit(self)
        self.products.setPlaceholderText("Ex: CHAPA FINA FRIO, TUBO ACO INOX")

        form.addRow("Empresa:", self.company)
        form.addRow("E-mail:", self.email)
        form.addRow("Contato:", self.contact)
        form.addRow("Telefone:", self.phone)
        form.addRow("Produtos:", self.products)

        actions = QHBoxLayout()
        actions.addStretch(1)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Salvar")
        btn_save.setObjectName("accent")
        btn_save.clicked.connect(self.accept)
        actions.addWidget(btn_cancel)
        actions.addWidget(btn_save)
        root.addLayout(actions)


class SuppliersPage(QWidget):
    baseReloaded = Signal()

    SETTINGS_ORG = "ComprasVesper"
    SETTINGS_APP = "ComprasVesper"
    SETTINGS_VIEW_MODE = "ui/suppliers/view_mode"
    SETTINGS_LAST_QUERY = "ui/suppliers/last_query"
    SETTINGS_TABLE_HEADER_STATE = "ui/suppliers/table_header_state"

    def __init__(
        self,
        app_context: AppContext,
        *,
        on_open_quote: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.app_context = app_context
        self._on_open_quote = on_open_quote
        self._on_open_settings = on_open_settings
        self._on_status = on_status

        self._thread_pool = QThreadPool.globalInstance()
        self._loading = False
        self._all_suppliers: list[object] = []
        self._suggest_options: list[SuggestionOption] = []
        self._products_suggestions: list[str] = []
        self._autoload_attempted = False
        self._auto_repair_attempted = False
        self._auto_repair_note = ""
        self._auto_reload_enabled = os.environ.get("PYTEST_CURRENT_TEST") is None

        self._settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._view_mode = str(self._settings.value(self.SETTINGS_VIEW_MODE, "table") or "table").strip().lower()
        if self._view_mode not in {"table", "cards"}:
            self._view_mode = "cards"

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        # Busca responsiva sem recalcular o proxy a cada caractere digitado.
        self._search_timer.setInterval(260)
        self._search_timer.timeout.connect(self._apply_search)
        self._cards_relayout_timer = QTimer(self)
        self._cards_relayout_timer.setSingleShot(True)
        self._cards_relayout_timer.setInterval(0)
        self._cards_relayout_timer.timeout.connect(self._force_cards_relayout)
        self._toggle_guard = False

        self._edit_service = SupplierEditService(self.app_context)
        self._model = SupplierTableModel(edit_handler=self._edit_service.apply_edit, parent=self)
        self._proxy = SupplierFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        self._build_ui()
        self._wire_model()
        self.state_stack.set_loading("Preparando fornecedores...")
        # Evita travada visual no primeiro clique em Fornecedores: a página aparece
        # primeiro, e a carga/modelagem roda no próximo ciclo do event loop.
        QTimer.singleShot(0, self._reload_if_needed)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 12)
        root.setSpacing(10)

        title = QLabel("Fornecedores")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel("Busque por produto, empresa ou e-mail. Selecione e monte a cotação sem abrir planilha.")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)

        top_actions = QHBoxLayout()
        self.btn_new_supplier = QPushButton("Novo fornecedor")
        self.btn_new_supplier.setObjectName("secondarySmall")
        self.btn_new_supplier.setIcon(QIcon(":/icons/add.svg"))
        self.btn_new_supplier.setAccessibleName("Cadastrar novo fornecedor")
        self.btn_new_supplier.setToolTip("Abre o cadastro de um fornecedor que ainda não está na lista.")
        self.btn_new_supplier.clicked.connect(self.create_supplier)
        top_actions.addWidget(self.btn_new_supplier)

        self.btn_reload = QPushButton("Atualizar base")
        self.btn_reload.setAccessibleName("Atualizar lista de fornecedores")
        self.btn_reload.setToolTip("Atualiza a planilha e a lista local. Use se alguém alterou fornecedores no arquivo compartilhado.")
        self.btn_reload.clicked.connect(lambda: self._reload_base(force_refresh=True))
        top_actions.addWidget(self.btn_reload)
        self.btn_reload.setObjectName("secondarySmall")

        self.btn_toggle_view = QPushButton("Ver como tabela")
        self.btn_toggle_view.setIcon(QIcon(":/icons/layout.svg"))
        self.btn_toggle_view.setAccessibleName("Alternar entre tabela e cartões")
        self.btn_toggle_view.setToolTip("Mostra os fornecedores em tabela (detalhada) ou em cartões (mais visual).")
        self.btn_toggle_view.clicked.connect(self.toggle_view_mode)
        top_actions.addWidget(self.btn_toggle_view)

        top_actions.addStretch(1)
        root.addLayout(top_actions)

        search_row = QHBoxLayout()
        self.search_field = SmartSuggestLineEdit(self, debounce_ms=150, allow_manual=True)
        self.search_field.set_placeholder_text("Buscar produto, empresa ou e-mail. Ex: chapa, painel, rolamento...")
        self.search_field.set_provider(self._provider_search)
        self.search_field.committed.connect(self._on_search_committed)
        self.search_field.changed.connect(self._on_search_changed)
        search_row.addWidget(self.search_field, 1)

        self.btn_search = QPushButton("Buscar")
        self.btn_search.setAccessibleName("Aplicar filtro de busca")
        self.btn_search.setToolTip("Filtra a lista pelas palavras digitadas acima.")
        self.btn_search.clicked.connect(self._apply_search)
        search_row.addWidget(self.btn_search)

        self.btn_clear = QPushButton("Limpar")
        self.btn_clear.setAccessibleName("Limpar campo de busca")
        self.btn_clear.setToolTip("Apaga o texto da busca e mostra todos os fornecedores novamente.")
        self.btn_clear.clicked.connect(self.clear_search)
        search_row.addWidget(self.btn_clear)
        root.addLayout(search_row)

        self.state_stack = PageStateStack(self)
        root.addWidget(self.state_stack, 1)

        content = QFrame(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        self.view_stack = QStackedWidget(content)
        content_layout.addWidget(self.view_stack, 1)

        table_card = QFrame(content)
        table_card.setObjectName("pageCard")
        table_box = QVBoxLayout(table_card)
        table_box.setContentsMargins(8, 8, 8, 8)
        self.table = QTableView(table_card)
        self.table.setModel(self._proxy)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Sem stylesheet inline: os estados de hover/seleção vêm do ThemeManager
        # para respeitar claro/escuro e evitar texto ilegível.
        self.table.setMouseTracking(True)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnHidden(SupplierColumns.EMAIL, False)
        self.table.setColumnHidden(SupplierColumns.SCORE, True)
        table_box.addWidget(self.table, 1)
        self.view_stack.addWidget(table_card)

        cards_card = QFrame(content)
        cards_card.setObjectName("pageCard")
        cards_box = QVBoxLayout(cards_card)
        cards_box.setContentsMargins(8, 8, 8, 8)
        self.cards_view = QListView(cards_card)
        self.cards_view.setModel(self._proxy)
        self.cards_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cards_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cards_view.setWordWrap(True)
        self.cards_view.setUniformItemSizes(False)
        self.cards_view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.cards_view.setSpacing(6)
        cards_box.addWidget(self.cards_view, 1)
        self.view_stack.addWidget(cards_card)

        bottom = QHBoxLayout()
        self.btn_select_page = QPushButton("Selecionar resultados")
        self.btn_select_page.clicked.connect(self._select_visible)
        bottom.addWidget(self.btn_select_page)

        self.btn_unselect_page = QPushButton("Limpar seleção")
        self.btn_unselect_page.clicked.connect(self._unselect_visible)
        bottom.addWidget(self.btn_unselect_page)

        self.count_label = QLabel("0 fornecedores")
        self.count_label.setObjectName("muted")
        bottom.addWidget(self.count_label)

        bottom.addStretch(1)
        self.btn_quote = QPushButton("Montar cotação")
        self.btn_quote.setIcon(QIcon(":/icons/open.svg"))
        self.btn_quote.setObjectName("accent")
        self.btn_quote.setAccessibleName("Montar cotação para os fornecedores selecionados")
        self.btn_quote.setToolTip("Abre a tela para montar o e-mail de cotação para os fornecedores marcados.")
        self.btn_quote.clicked.connect(self._open_quote)
        self.btn_quote.setEnabled(False)
        bottom.addWidget(self.btn_quote)
        content_layout.addLayout(bottom)

        self.state_stack.set_content_widget(content)

        # Ocultar botões redundantes conforme refino de UX de fornecedores
        self.btn_reload.hide()
        self.btn_toggle_view.hide()
        self.btn_search.hide()
        self.btn_clear.hide()
        self._set_view_mode("table", persist=False)

    def _wire_model(self) -> None:
        self._select_delegate = SupplierSelectDelegate(parent=self.table)
        self.table.setItemDelegateForColumn(SupplierColumns.SELECT, self._select_delegate)
        self._products_delegate = SupplierProductsDelegate(parent=self.table)
        self.table.setItemDelegateForColumn(SupplierColumns.PRODUCTS, self._products_delegate)
        self._cards_delegate = SupplierCardDelegate(self.cards_view)
        self.cards_view.setItemDelegate(self._cards_delegate)

        self.cards_view.setSelectionModel(self.table.selectionModel())
        self.table.clicked.connect(self._on_table_clicked)
        self.cards_view.clicked.connect(self._on_cards_clicked)
        self.cards_view.installEventFilter(self)
        self.cards_view.viewport().installEventFilter(self)
        self.table.viewport().installEventFilter(self)

        self._model.selectedEmailsChanged.connect(self._on_selected_emails_changed)
        self._model.rowEditFailed.connect(lambda message: self._warn("Edicao", message))
        self._model.rowPersisted.connect(self._on_row_persisted)

        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionsClickable(True)
        header.setSectionResizeMode(SupplierColumns.SELECT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(SupplierColumns.PRODUCTS, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(SupplierColumns.COMPANY, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(SupplierColumns.CONTACT, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(SupplierColumns.EMAIL, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(SupplierColumns.PHONE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(SupplierColumns.SCORE, QHeaderView.ResizeMode.Fixed)

        self._restore_table_header_state()
        header.sectionMoved.connect(lambda *_args: self._save_table_header_state())
        header.sectionResized.connect(lambda *_args: self._save_table_header_state())


    def on_page_activated(self) -> None:
        # A tela pode ter sido aberta antes do prewarm terminar. Se a base ficou
        # pronta em segundo plano, atualiza sem forçar leitura de Excel/NAS.
        if not self._all_suppliers and self._index_supplier_count_safe() > 0:
            QTimer.singleShot(0, self._reload_if_needed)

    def _index_supplier_count_safe(self) -> int:
        try:
            index = getattr(self.app_context.state, "index", None)
            if index is None:
                return 0
            if hasattr(index, "supplier_count"):
                return int(getattr(index, "supplier_count") or 0)
            if hasattr(index, "get_all_suppliers"):
                return len(list(index.get_all_suppliers() or []))
            return len(list(getattr(index, "suppliers", []) or []))
        except Exception:
            return 0

    def _reload_if_needed(self) -> None:
        try:
            self._edit_service.flush_pending_master_sync(max_items=20)
        except Exception:
            pass
        suppliers = self._load_suppliers()
        if suppliers:
            self._all_suppliers = suppliers
            self._refresh_suggest_options()
            self._refresh_product_suggestions()
            self._refresh_model_rows()
            self.btn_reload.hide()
            self._restore_initial_query()
        else:
            self._all_suppliers = []
            self._refresh_suggest_options()
            self._refresh_product_suggestions()
            self._refresh_model_rows()
            self.state_stack.set_empty("Base ainda não carregada. Atualize uma vez para preparar o cache local.")
            self.btn_reload.show()
            self._set_status("Base não carregada. Use 'Atualizar base' para preparar fornecedores.")
        # Não recarrega XLSX/NAS automaticamente se já há índice local pronto.
        # Antes isso causava a travada sentida ao entrar pela primeira vez em
        # Fornecedores. Atualização completa agora fica explícita ou só ocorre
        # quando não existe base carregada.
        if self._auto_reload_enabled and not self._autoload_attempted and not suppliers:
            self._autoload_attempted = True
            QTimer.singleShot(0, lambda: self._reload_base(force_refresh=False))

    def _restore_initial_query(self) -> None:
        # Não restaurar busca antiga automaticamente: isso dava impressão de lista incompleta.
        self.search_field.set_value("")
        self._apply_search()

    def _load_suppliers(self) -> list[object]:
        index = self.app_context.state.index
        if hasattr(index, "get_all_suppliers"):
            try:
                return list(index.get_all_suppliers())
            except Exception:
                return []
        return list(getattr(index, "suppliers", []) or [])

    def _refresh_model_rows(self) -> None:
        rows = self._edit_service.build_rows(self._all_suppliers, self.app_context.state.selected_emails)
        was_sorting = bool(self.table.isSortingEnabled())
        self.table.setUpdatesEnabled(False)
        self.cards_view.setUpdatesEnabled(False)
        if was_sorting:
            self.table.setSortingEnabled(False)
        try:
            self._model.set_rows(rows)
        finally:
            if was_sorting:
                self.table.setSortingEnabled(True)
            self.table.setUpdatesEnabled(True)
            self.cards_view.setUpdatesEnabled(True)
        if self.table.selectionModel() is not None and self.cards_view.selectionModel() is None:
            self.cards_view.setSelectionModel(self.table.selectionModel())
        self._schedule_cards_relayout()
        self._update_count_label()

    def _provider_search(self, query: str, force: bool) -> list[SuggestionOption]:
        return resolve_suggestions(self._suggest_options, query, force=force, limit=12)

    def _on_search_committed(self, text: str, _from_catalog: bool, _payload: Any) -> None:
        query = _clean(text)
        if query:
            self.app_context.state.config.add_search_to_history(query)
        self._apply_search()

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_search(self) -> None:
        query = _clean(self.search_field.value())
        self._proxy.set_filter_text(query)
        self._settings.setValue(self.SETTINGS_LAST_QUERY, query)
        self._update_count_label()
        shown = self._proxy.rowCount()
        total = len(self._all_suppliers)
        if shown == 0:
            self.state_stack.set_empty("Nenhum fornecedor encontrado para esta busca.")
        else:
            self.state_stack.show_content()
        self._set_status(f"{total} fornecedores | {shown} resultado(s)")

    def _refresh_suggest_options(self) -> None:
        options = supplier_context_options(self._all_suppliers)
        seen: set[str] = set()
        for option in options:
            seen.add(normalize_text(option.value))

        for value in self.app_context.state.config.search_history or []:
            text = _clean(value)
            norm = normalize_text(text)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            options.insert(0, SuggestionOption(label=text, value=text))

        for row in self._model.rows():
            for product in row.products:
                norm = normalize_text(product)
                if norm in seen:
                    continue
                seen.add(norm)
                options.append(SuggestionOption(label=product, value=product))
        self._suggest_options = options

    def _refresh_product_suggestions(self) -> None:
        values: list[str] = []
        seen: set[str] = set()
        for row in self._model.rows():
            for product in row.products:
                norm = normalize_text(product)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                values.append(product)
        self._products_suggestions = values
        self._products_delegate.set_suggestions(values)

    def _current_source_row(self):
        selection = self.table.selectionModel()
        if selection is None:
            return None
        indexes = selection.selectedRows()
        if not indexes:
            return None
        proxy_index = indexes[0]
        source_index = self._proxy.mapToSource(proxy_index)
        return self._model.row_at(source_index.row())

    def _on_table_clicked(self, index) -> None:
        if not index.isValid():
            return
        self._toggle_proxy_row_selection(index.row())

    def _on_cards_clicked(self, index) -> None:
        if not index.isValid():
            return
        self.cards_view.setCurrentIndex(index)
        self._toggle_proxy_row_selection(index.row())

    def _toggle_proxy_row_selection(self, proxy_row: int) -> None:
        if self._toggle_guard:
            return
        self._toggle_guard = True
        try:
            self._toggle_proxy_row_selection_impl(proxy_row)
        finally:
            self._toggle_guard = False

    def _toggle_proxy_row_selection_impl(self, proxy_row: int) -> None:
        check_index = self._proxy.index(proxy_row, SupplierColumns.SELECT)
        if not check_index.isValid():
            return
        row = self._model.row_at(self._proxy.mapToSource(check_index).row())
        if row is None:
            return
        if not _clean(row.email):
            self._set_status(f"{row.company} sem e-mail. Edite o campo E-mail para habilitar envio.")
            return
        state = self._proxy.data(check_index, Qt.ItemDataRole.CheckStateRole)
        next_state = Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked
        self._proxy.setData(check_index, next_state, Qt.ItemDataRole.CheckStateRole)
        if next_state == Qt.CheckState.Checked:
            self._set_status(f"Selecionado para cotacao: {row.company}")
        else:
            self._set_status(f"Removido da cotacao: {row.company}")

    def _on_selected_emails_changed(self, emails: object) -> None:
        if isinstance(emails, set):
            self.app_context.state.selected_emails = set(emails)
        self._update_count_label()

    def _update_count_label(self) -> None:
        shown = self._proxy.rowCount()
        selected = len(self.app_context.state.selected_emails)
        self.count_label.setText(f"{shown} resultado(s) • {selected} selecionado(s)")
        self.btn_quote.setText(f"Montar cotação ({selected})")
        self.btn_quote.setEnabled(selected > 0)

    def _visible_source_rows(self) -> list[int]:
        rows: list[int] = []
        for proxy_row in range(self._proxy.rowCount()):
            proxy_index = self._proxy.index(proxy_row, SupplierColumns.SELECT)
            source_index = self._proxy.mapToSource(proxy_index)
            if source_index.isValid():
                rows.append(source_index.row())
        return rows

    def _set_visible_selected(self, selected: bool) -> None:
        source_rows = self._visible_source_rows()
        self.table.setUpdatesEnabled(False)
        self.cards_view.setUpdatesEnabled(False)
        try:
            self._model.set_selected_rows(source_rows, selected)
        finally:
            self.table.setUpdatesEnabled(True)
            self.cards_view.setUpdatesEnabled(True)
            self.table.viewport().update()
            self.cards_view.viewport().update()
        self._update_count_label()

    def _select_visible(self) -> None:
        self._set_visible_selected(True)

    def _unselect_visible(self) -> None:
        self._set_visible_selected(False)

    def _open_quote(self) -> None:
        if self._on_open_quote:
            self._on_open_quote()
        else:
            self._info("Cotação", "Navegação para cotação indisponível nesta tela.")

    def _open_settings(self) -> None:
        if self._on_open_settings:
            self._on_open_settings()

    def _reload_base(self, *, force_refresh: bool) -> None:
        if self._loading:
            return

        self._loading = True
        self.btn_reload.setEnabled(False)
        self.btn_reload.setText("Atualizando...")
        self.state_stack.set_loading("Atualizando base de fornecedores...")
        self._set_status("Atualizando base de fornecedores...")

        signals = _ReloadSignals(self)
        signals.done.connect(self._on_reload_done)
        signals.error.connect(self._on_reload_error)

        worker = _ReloadRunnable(
            app_context=self.app_context,
            force_refresh=force_refresh,
            signals=signals,
        )
        self._thread_pool.start(worker)

    def _on_reload_done(self, result: Any) -> None:
        self._loading = False
        self.btn_reload.setEnabled(True)
        self.btn_reload.setText("Atualizar base")

        suppliers_count = int(getattr(result, "suppliers_count", 0) or 0)
        status_message = _clean(getattr(result, "status_message", ""))
        warnings = list(getattr(result, "warnings", []) or [])
        errors = list(getattr(result, "errors", []) or [])

        self._all_suppliers = self._load_suppliers()
        self._refresh_suggest_options()
        self._refresh_model_rows()
        self._refresh_product_suggestions()
        self._apply_search()

        # If user has an old persisted query that hides all rows, clear once.
        if suppliers_count > 0 and self._proxy.rowCount() == 0 and _clean(self.search_field.value()):
            self.search_field.set_value("")
            self._apply_search()

        if errors:
            self._warn("Base", "Falhas ao carregar base:\n" + "\n".join(errors[:5]))
        elif warnings:
            self._warn("Base", "Base carregada com avisos:\n" + "\n".join(warnings[:5]))

        if self._proxy.rowCount() > 0:
            self.state_stack.show_content()
        else:
            if not self._auto_repair_attempted:
                self._auto_repair_attempted = True
                try:
                    boot = ensure_runtime_bootstrap(self.app_context.state.config, force_refresh=True)
                    parts = [boot.message] + list(boot.warnings or [])
                    self._auto_repair_note = " | ".join([p for p in parts if _clean(p)])
                except Exception as exc:
                    self._auto_repair_note = f"bootstrap_falhou: {exc}"
                self.state_stack.set_loading("Preparando base local neste usuario...")
                self._set_status("Tentando auto-reparo da base de fornecedores...")
                QTimer.singleShot(50, lambda: self._reload_base(force_refresh=True))
                return
            details = _clean(self._auto_repair_note)
            if details:
                self.state_stack.set_empty(f"Base sem fornecedores visiveis. Diagnostico: {details}")
            else:
                self.state_stack.set_empty("Base carregada, mas sem fornecedores visiveis.")
        self._schedule_cards_relayout()

        suffix = f" | {status_message}" if status_message else ""
        self._set_status(f"Base carregada: {suppliers_count} fornecedor(es){suffix}")
        self.baseReloaded.emit()

    def _on_reload_error(self, message: str) -> None:
        self._loading = False
        self.btn_reload.setEnabled(True)
        self.btn_reload.setText("Atualizar base")
        self.state_stack.set_error("Falha ao atualizar base de fornecedores.")
        self._error("Base", f"Falha ao atualizar base: {message}")
        self._set_status("Falha ao atualizar base")

    def _set_view_mode(self, mode: str, *, persist: bool = True) -> None:
        mode_norm = "cards" if mode == "cards" else "table"
        self._view_mode = mode_norm
        self.view_stack.setCurrentIndex(1 if mode_norm == "cards" else 0)
        self.btn_toggle_view.setText("Ver como tabela" if mode_norm == "cards" else "Ver como cartões")
        if mode_norm == "cards":
            self._schedule_cards_relayout()
        if persist:
            self._settings.setValue(self.SETTINGS_VIEW_MODE, mode_norm)

    def toggle_view_mode(self) -> None:
        self._set_view_mode("cards" if self._view_mode == "table" else "table")

    def clear_search(self) -> None:
        self.search_field.set_value("")
        self._apply_search()

    def focus_search(self) -> None:
        self.search_field.focus_entry()

    def create_supplier(self) -> None:
        dialog = _NewSupplierDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ok, message, _row = self._edit_service.create_local_supplier(
            company=dialog.company.text(),
            email=dialog.email.text(),
            contact=dialog.contact.text(),
            phone=dialog.phone.text(),
            products_text=dialog.products.text(),
        )
        if not ok:
            self._error("Novo fornecedor", message)
            return
        self._set_status("Fornecedor local criado com sucesso.")
        self._reload_base(force_refresh=False)

    def copy_selected_email(self) -> None:
        row = self._current_source_row()
        if row is None or not row.email:
            self._warn("Copiar e-mail", "Selecione um fornecedor com e-mail.")
            return
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(row.email)
        self._set_status(f"E-mail copiado: {row.email}")

    def open_selected_site(self) -> None:
        row = self._current_source_row()
        if row is None:
            self._warn("Abrir site", "Selecione um fornecedor primeiro.")
            return
        supplier = row.raw_supplier
        site = _clean(getattr(supplier, "site", "")) if supplier is not None else ""
        if not site:
            site = _clean(getattr(supplier, "endereco", "")) if supplier is not None else ""
        if not site:
            self._warn("Abrir site", "Fornecedor sem site/endereco web informado.")
            return
        if site.startswith("www."):
            site = f"https://{site}"
        if not site.startswith("http://") and not site.startswith("https://"):
            self._warn("Abrir site", "Endereço não parece um link web válido.")
            return
        try:
            webbrowser.open(site, new=2)
            self._set_status(f"Abrindo site: {site}")
        except Exception as exc:
            self._error("Abrir site", f"Não foi possível abrir o link.\n{exc}")

    def open_admin(self) -> None:
        dialog = AdminAuditDialog(
            app_context=self.app_context,
            edit_service=self._edit_service,
            on_status=self._on_status,
            parent=self,
        )
        dialog.overridesChanged.connect(self._reload_rows_preserving_query)
        dialog.exec()

    def _reload_rows_preserving_query(self) -> None:
        self._all_suppliers = self._load_suppliers()
        self._refresh_model_rows()
        self._apply_search()

    def _set_status(self, text: str) -> None:
        if self._on_status:
            self._on_status(text)

    def _on_row_persisted(self, _supplier_key: str) -> None:
        self._refresh_product_suggestions()
        self._refresh_suggest_options()
        self._update_count_label()
        self._set_status("Fornecedor atualizado com sucesso.")

    def _restore_table_header_state(self) -> None:
        # v2.2: não reaproveita estado antigo do cabeçalho porque larguras salvas
        # corrompidas faziam colunas/linhas parecerem ausentes em alguns PCs.
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        self.table.setColumnWidth(SupplierColumns.SELECT, 38)
        self.table.setColumnWidth(SupplierColumns.PRODUCTS, 260)
        self.table.setColumnWidth(SupplierColumns.COMPANY, 310)
        self.table.setColumnWidth(SupplierColumns.CONTACT, 180)
        self.table.setColumnWidth(SupplierColumns.EMAIL, 280)
        self.table.setColumnWidth(SupplierColumns.PHONE, 190)
        self.table.setColumnWidth(SupplierColumns.SCORE, 84)

    def _save_table_header_state(self) -> None:
        try:
            state = self.table.horizontalHeader().saveState()
            self._settings.setValue(self.SETTINGS_TABLE_HEADER_STATE, state)
        except Exception:
            pass

    def _schedule_cards_relayout(self) -> None:
        if not hasattr(self, "_cards_delegate"):
            return
        self._cards_delegate.clear_size_cache()
        self.cards_view.scheduleDelayedItemsLayout()
        self._cards_relayout_timer.start()

    def _force_cards_relayout(self) -> None:
        try:
            self.cards_view.doItemsLayout()
        except Exception:
            pass

    def _event_pos_for_view(self, event):
        try:
            return event.position().toPoint()
        except Exception:
            try:
                return event.pos()
            except Exception:
                return None

    def _handle_view_mouse_release(self, view, event) -> bool:
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            pos = self._event_pos_for_view(event)
            if pos is None:
                return False
            index = view.indexAt(pos)
            if not index.isValid():
                return False
            view.setCurrentIndex(index)
            self._toggle_proxy_row_selection(index.row())
            event.accept()
            return True
        except Exception:
            return False

    def eventFilter(self, watched, event):  # noqa: N802
        if watched in (self.cards_view, self.cards_view.viewport()) and event.type() == QEvent.Type.Resize:
            self._schedule_cards_relayout()
        # P0 seleção confiável: captura o clique no viewport inteiro. Assim,
        # clicar em qualquer célula/área visível da linha/card alterna a seleção
        # uma única vez, independente do estilo da checkbox/delegate.
        if watched is self.table.viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            if self._handle_view_mouse_release(self.table, event):
                return True
        if watched is self.cards_view.viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            if self._handle_view_mouse_release(self.cards_view, event):
                return True
        return super().eventFilter(watched, event)

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _error(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)
