from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.application.context import AppContext
from app.core.companies import COMPANIES, company_for_key
from app.core.email_signature import first_signature_owner, signature_owner_options
from app.core.signature_identity import current_signature_identity, resolve_signature_owner
from app.core.smart_parser import REQUEST_FREIGHT, REQUEST_MATERIAL, REQUEST_PURCHASE_ORDER
from app.core.update_service import UpdateService

from .pages import (
    HistoryPage,
    ModelsSignaturesPage,
    NewQuoteHomePage,
    NewRequestPage,
    SettingsPage,
    SuppliersPage,
)
from .theme import ThemeManager
from .icon_utils import get_icon_char, get_icon
from .ui_scale import font_css, scaled_px, scaled_window_size, ui_scale_profile
from .widgets.vesper_select import VesperSelect
from .workers import UpdateWorker, UpdateWorkerSignals

REQUEST_EX_PANELS = "ex_panels"


@dataclass(frozen=True)
class _PageSpec:
    key: str
    title: str
    icon: str
    subtitle: str


# Navegação operacional enxuta: o uso diário começa em Nova cotação.
# Telas administrativas existem no stack, mas não competem na sidebar.
PAGES = [
    _PageSpec("new_request", "Nova cotação", get_icon_char("new_request"), "Escolha o que será enviado."),
    _PageSpec("history", "Acompanhar", get_icon_char("history"), "Respostas, pendências e cobrança."),
    _PageSpec("suppliers", "Fornecedores", get_icon_char("suppliers"), "Busca por produto, empresa ou e-mail."),
]

HIDDEN_PAGES = [
    _PageSpec("models", "Modelos e assinaturas", get_icon_char("models"), "Textos e assinaturas."),
    _PageSpec("settings", "Configurações", get_icon_char("settings"), "Ajustes essenciais e avançados."),
]

ALL_PAGES = PAGES + HIDDEN_PAGES



class _SupplierPrewarmSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class _SupplierPrewarmRunnable(QRunnable):
    def __init__(self, app_context: AppContext, signals: _SupplierPrewarmSignals) -> None:
        super().__init__()
        self.app_context = app_context
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.app_context.reload_base_uc.execute(force_refresh=False)
            try:
                self.signals.done.emit(result)
            except RuntimeError:
                pass
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass


class MainWindow(QMainWindow):
    """Shell v1.6 com sidebar única, dashboard inicial e composer embutido."""

    def __init__(self, theme_manager: ThemeManager, app_context: AppContext | None = None):
        super().__init__()
        self.theme_manager = theme_manager
        self.app_context = app_context or AppContext.bootstrap()
        self.setWindowTitle("ComprasVesper")
        self.resize(*scaled_window_size(1560, 900, min_width=1180, min_height=720))
        self.setMinimumSize(*scaled_window_size(1180, 720, min_width=980, min_height=640))
        if ui_scale_profile().compact:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

        self._update_service = UpdateService(self.app_context.state.config)
        self._update_thread_pool = QThreadPool.globalInstance()
        self._update_signals: UpdateWorkerSignals | None = None
        self._update_state = "idle"
        self._update_payload: dict[str, Any] = {}
        self._update_job_running = False
        self._composer_page: NewRequestPage | None = None
        self._current_nav_key = "new_request"
        self._page_indexes: Dict[str, int] = {}
        self._page_buttons: Dict[str, QPushButton] = {}
        self._page_factories: Dict[str, Callable[[], QWidget]] = {}
        self._loaded_pages: set[str] = set()
        self._pending_composer_request_type = REQUEST_MATERIAL

        root = QWidget(self)
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_top_bar(), 0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = self._build_sidebar()
        body.addWidget(self.sidebar, 0)

        self.body = QFrame(root)
        self.body.setObjectName("mainContentSurface")
        content_layout = QVBoxLayout(self.body)
        content_layout.setContentsMargins(scaled_px(12), scaled_px(12), scaled_px(12), scaled_px(8))
        content_layout.setSpacing(scaled_px(8))

        self.update_banner = QFrame(self.body)
        self.update_banner.setObjectName("pageCard")
        banner_box = QHBoxLayout(self.update_banner)
        banner_box.setContentsMargins(scaled_px(10), scaled_px(8), scaled_px(10), scaled_px(8))
        self.update_banner_label = QLabel("")
        self.update_banner_label.setWordWrap(True)
        banner_box.addWidget(self.update_banner_label, 1)
        self.update_banner_action = QPushButton("Aguarde")
        self.update_banner_action.setMinimumWidth(scaled_px(140))
        self.update_banner_action.clicked.connect(self._handle_update_banner_action)
        banner_box.addWidget(self.update_banner_action, 0)
        self.update_banner.hide()
        content_layout.addWidget(self.update_banner, 0)

        self.stack = QStackedWidget(self.body)
        content_layout.addWidget(self.stack, 1)
        body.addWidget(self.body, 1)
        root_layout.addLayout(body, 1)

        self._build_pages()
        self._install_global_shortcuts()
        self._set_page("new_request")
        self._load_identity_controls()
        self.statusBar().showMessage("Pronto")
        self.statusBar().hide()
        QTimer.singleShot(0, self.theme_manager.repolish)
        QTimer.singleShot(0, self._start_update_flow)
        QTimer.singleShot(0, self._prewarm_supplier_base)
        QTimer.singleShot(2500, self._sync_replies_in_background)
        self._imap_periodic_timer = QTimer(self)
        self._imap_periodic_timer.setInterval(120000)
        self._imap_periodic_timer.timeout.connect(self._sync_replies_in_background)
        self._imap_periodic_timer.start()
        QTimer.singleShot(6000, self._process_smtp_queue_in_background)
        self._smtp_queue_timer = QTimer(self)
        self._smtp_queue_timer.setInterval(180000)
        self._smtp_queue_timer.timeout.connect(self._process_smtp_queue_in_background)
        self._smtp_queue_timer.start()

    # ---------- Shell ----------
    def _build_top_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("topBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(scaled_px(18), scaled_px(10), scaled_px(18), scaled_px(10))
        row.setSpacing(scaled_px(12))
        logo = QLabel("V")
        logo.setObjectName("brandLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(logo, 0)
        title = QLabel("Compras Vesper")
        title.setStyleSheet(font_css(22, 850))
        row.addWidget(title, 0)
        row.addStretch(1)

        self.company_combo = VesperSelect(bar, visible_rows=2)
        self.company_combo.addItem(COMPANIES["vesper"].display_name, "vesper")
        self.company_combo.addItem(COMPANIES["ventrio"].display_name, "ventrio")
        self._prepare_top_combo(self.company_combo, visible_rows=2)
        self.company_combo.currentIndexChanged.connect(self._on_global_company_changed)
        row.addWidget(self._labeled_control("Empresa", self.company_combo), 0)

        self.signature_combo = VesperSelect(bar, visible_rows=5)
        self._prepare_top_combo(self.signature_combo, visible_rows=5)
        self.signature_combo.currentTextChanged.connect(self._on_global_signature_changed)
        row.addWidget(self._labeled_control("Assinatura", self.signature_combo), 0)

        help_btn = QPushButton("?  Ajuda")
        help_btn.setObjectName("topLinkButton")
        help_btn.clicked.connect(self._show_help)
        row.addWidget(help_btn, 0)
        more = QPushButton("⋯")
        more.setObjectName("topMoreButton")
        more.clicked.connect(self._open_more_menu)
        row.addWidget(more, 0)
        return bar


    def _prepare_top_combo(self, combo: QComboBox, *, visible_rows: int = 5) -> None:
        """Compatibilidade: VesperSelect já controla popup/largura; combos antigos recebem ajuste simples."""
        combo.setMaxVisibleItems(max(1, int(visible_rows)))
        combo.setMinimumWidth(scaled_px(230))
        combo.setMinimumHeight(scaled_px(42))
        try:
            view = combo.view()
            if view is not None:
                view.setObjectName("comboPopup")
                view.setUniformItemSizes(True)
                view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        except Exception:
            pass

    def _prewarm_supplier_base(self) -> None:
        # O usuário usa Nova cotação antes de Fornecedores. Portanto, a base precisa
        # estar aquecida no início, sem depender de abrir outra tela. O worker usa
        # cache local quando disponível e não bloqueia a interface.
        if getattr(self, "_supplier_prewarm_running", False):
            return
        try:
            index = getattr(self.app_context.state, "index", None)
            if index is not None and hasattr(index, "suppliers") and len(getattr(index, "suppliers") or []) > 0:
                return
        except Exception:
            pass
        self._supplier_prewarm_running = True
        signals = _SupplierPrewarmSignals(self)
        self._supplier_prewarm_signals = signals
        signals.done.connect(self._on_supplier_prewarm_done)
        signals.error.connect(self._on_supplier_prewarm_error)
        QThreadPool.globalInstance().start(_SupplierPrewarmRunnable(self.app_context, signals))

    def _on_supplier_prewarm_done(self, _result: object) -> None:
        self._supplier_prewarm_running = False
        if self._composer_page is not None and hasattr(self._composer_page, "_refresh_supplier_suggestions"):
            try:
                self._composer_page._refresh_supplier_suggestions()
            except Exception:
                pass

    def _on_supplier_prewarm_error(self, _message: str) -> None:
        self._supplier_prewarm_running = False

    def _trigger_initial_base_load(self) -> None:
        # Mantido por compatibilidade: não pré-carrega fornecedores no startup.
        self._update_top_status()

    def _on_suppliers_base_reloaded(self) -> None:
        self._update_top_status()

    def _update_top_status(self) -> None:
        # Status técnico de base/e-mail fica em Configurações/Avançado.
        # A interface comum só deve avisar quando alguma ação falhar.
        return

    def _labeled_control(self, label: str, widget: QWidget) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("topControlWrap")
        box = QVBoxLayout(frame)
        box.setContentsMargins(scaled_px(12), scaled_px(8), scaled_px(12), scaled_px(10))
        box.setSpacing(scaled_px(5))
        lab = QLabel(label)
        lab.setObjectName("topControlLabel")
        box.addWidget(lab)
        box.addWidget(widget)
        return frame

    def _build_sidebar(self) -> QFrame:
        side = QFrame(self)
        side.setObjectName("sideNav")
        side.setFixedWidth(scaled_px(264))
        box = QVBoxLayout(side)
        box.setContentsMargins(scaled_px(14), scaled_px(20), scaled_px(14), scaled_px(14))
        box.setSpacing(scaled_px(8))
        for spec in PAGES:
            btn = QPushButton(spec.title)
            btn.setIcon(get_icon(spec.key, color="#0B63CE", scale_factor=1.0))
            btn.setIconSize(QSize(scaled_px(18), scaled_px(18)))
            btn.setObjectName("sideNavButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(spec.subtitle)
            btn.clicked.connect(lambda _=False, key=spec.key: self._set_page(key))
            box.addWidget(btn, 0)
            self._page_buttons[spec.key] = btn
        box.addStretch(1)
        return side

    def _load_identity_controls(self) -> None:
        cfg = self.app_context.state.config
        company_key = str(getattr(cfg, "default_company_key", "vesper") or "vesper")
        idx = self.company_combo.findData(company_key)
        if idx >= 0:
            self.company_combo.setCurrentIndex(idx)
        owner_options = signature_owner_options(cfg)
        if not owner_options:
            owner_options = [first_signature_owner(cfg) or "Operador demo"]
        self.signature_combo.clear()
        self.signature_combo.addItems([item for item in owner_options if item])
        identity = current_signature_identity()
        resolved_owner, _source = resolve_signature_owner(cfg, identity)
        if resolved_owner:
            sig_idx = self.signature_combo.findText(resolved_owner)
            if sig_idx >= 0:
                self.signature_combo.setCurrentIndex(sig_idx)

    def _build_pages(self) -> None:
        for spec in ALL_PAGES:
            self._page_factories[spec.key] = lambda key=spec.key: self._create_page(key)
            page = self._create_page(spec.key) if spec.key == "new_request" else self._make_lazy_placeholder(spec)
            if spec.key == "new_request":
                self._loaded_pages.add(spec.key)
            idx = self.stack.addWidget(page)
            self._page_indexes[spec.key] = idx
        self._page_factories["composer"] = lambda: self._create_composer_page()
        idx = self.stack.addWidget(self._make_lazy_placeholder(_PageSpec("composer", "Nova cotação", "+", "Composer")))
        self._page_indexes["composer"] = idx

    def _create_page(self, key: str) -> QWidget:
        if key == "new_request":
            return NewQuoteHomePage(
                self.app_context,
                on_start=self._open_task,
                on_open_history=lambda: self._set_page("history"),
                on_open_suppliers=lambda: self._set_page("suppliers"),
                parent=self.stack,
            )
        if key == "suppliers":
            return SuppliersPage(
                self.app_context,
                on_open_quote=lambda: self._open_task(REQUEST_MATERIAL, import_selected=True),
                on_open_settings=lambda: self._set_page("settings"),
                on_status=self.statusBar().showMessage,
            )
        if key == "history":
            return HistoryPage(self.app_context, on_status=self.statusBar().showMessage, on_open_quote=self._reopen_event_in_composer)
        if key == "models":
            return ModelsSignaturesPage(self.app_context, on_status=self.statusBar().showMessage)
        if key == "settings":
            page = SettingsPage(self.app_context, on_status=self.statusBar().showMessage)
            page.bind_update_actions(on_install_update=self._install_update_from_ui, on_retry_download=self._retry_update_download)
            page.set_update_status(self._update_state, dict(self._update_payload))
            return page
        return self._make_lazy_placeholder(_PageSpec(key, key, "", "Página não encontrada."))

    def _visible_top_level_ids(self) -> set[int]:
        """IDs das janelas top-level legítimas antes de uma troca de tela.

        O Windows mostra qualquer QWidget sem parent como janela independente.
        No fluxo de Frete, uma janela órfã temporária era percebida como uma
        "janelinha" vazia. Guardamos o conjunto atual para conseguir ocultar
        apenas top-levels inesperados durante essa transição operacional.
        """
        try:
            return {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
        except Exception:
            return set()

    def _hide_unexpected_toplevels(self, baseline: set[int] | None = None) -> None:
        baseline = baseline or set()
        try:
            for widget in QApplication.topLevelWidgets():
                if id(widget) in baseline or widget is self:
                    continue
                # Popups nativos legítimos, menus e modais reais não fazem parte
                # da abertura de Frete. Se aparecer um QWidget pequeno e sem
                # modal, é artefato visual e deve ser ocultado.
                try:
                    if widget.isModal() or isinstance(widget, (QMenu, QMessageBox)):
                        continue
                except Exception:
                    pass
                try:
                    if widget.isVisible() and widget.windowTitle() in {"", "ComprasVesper"}:
                        widget.hide()
                        widget.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass

    def _create_composer_page(self) -> QWidget:
        self._composer_page = NewRequestPage(
            self.app_context,
            on_status=self.statusBar().showMessage,
            on_open_suppliers=lambda: self._set_page("suppliers"),
            on_open_history=lambda: self._set_page("history"),
            on_open_settings=lambda: self._set_page("settings"),
            on_cycle_theme=self._cycle_theme_mode,
            embedded_shell=True,
            initial_request_type=self._pending_composer_request_type or REQUEST_MATERIAL,
            parent=self.stack,
        )
        self._sync_global_identity_to_composer()
        return self._composer_page

    def _make_lazy_placeholder(self, spec: _PageSpec) -> QWidget:
        panel = QFrame(self.stack if hasattr(self, "stack") else self)
        panel.setObjectName("dashboardCard")
        box = QVBoxLayout(panel)
        box.setContentsMargins(scaled_px(18), scaled_px(18), scaled_px(18), scaled_px(18))
        title = QLabel(spec.title)
        title.setObjectName("pageTitle")
        box.addWidget(title)
        subtitle = QLabel("Carregando esta área somente quando necessário.")
        subtitle.setObjectName("muted")
        box.addWidget(subtitle)
        box.addStretch(1)
        return panel

    def _ensure_page_loaded(self, key: str) -> None:
        if key in self._loaded_pages:
            return
        idx = self._page_indexes.get(key)
        factory = self._page_factories.get(key)
        if idx is None or factory is None:
            return
        old = self.stack.widget(idx)
        page = factory()
        try:
            if page.parent() is None:
                page.setParent(self.stack)
        except Exception:
            pass
        self.stack.insertWidget(idx, page)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()
        self._loaded_pages.add(key)
        QTimer.singleShot(0, self.theme_manager.repolish)

    def _set_page(self, key: str) -> None:
        self._ensure_page_loaded(key)
        idx = self._page_indexes.get(key)
        if idx is None:
            return
        self.stack.setCurrentIndex(idx)
        self._current_nav_key = key if key != "composer" else "new_request"
        self._refresh_nav_state()
        page = self.stack.currentWidget()
        if hasattr(page, "on_page_activated"):
            try:
                page.on_page_activated()
            except Exception:
                pass
        self._animate_current_page()
        QTimer.singleShot(0, self.theme_manager.repolish)
        self._sync_global_identity_to_composer()

    def _open_task(self, request_type: str, *, import_selected: bool = False) -> None:
        # Se o composer ainda não foi criado, já nasce no tipo correto. Antes a
        # primeira abertura sempre construía Material e depois trocava para
        # Frete/Painéis/OC; em Frete isso era perceptível porque as 7
        # transportadoras padrão eram desenhadas logo em seguida.
        baseline_top_levels = self._visible_top_level_ids()
        if request_type == REQUEST_FREIGHT:
            self.setUpdatesEnabled(False)
        try:
            self._pending_composer_request_type = request_type
            self._ensure_page_loaded("composer")
            if self._composer_page is not None:
                self._sync_global_identity_to_composer()
                try:
                    self._composer_page.set_request_type_public(request_type)
                except Exception:
                    pass
                if import_selected and hasattr(self._composer_page, "import_selected_emails_from_state"):
                    try:
                        self._composer_page.import_selected_emails_from_state()
                    except Exception:
                        pass
            self._set_page("composer")
        finally:
            if request_type == REQUEST_FREIGHT:
                self.setUpdatesEnabled(True)
                self.update()
                self._hide_unexpected_toplevels(baseline_top_levels)
                QTimer.singleShot(0, lambda b=baseline_top_levels: self._hide_unexpected_toplevels(b))
                QTimer.singleShot(80, lambda b=baseline_top_levels: self._hide_unexpected_toplevels(b))

    def _refresh_nav_state(self) -> None:
        for key, btn in self._page_buttons.items():
            btn.setProperty("active", key == self._current_nav_key)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _animate_current_page(self) -> None:
        # Navegação deve ser instantânea. Animação de opacidade deixava a tela anterior
        # visível em alguns PCs enquanto a nova página era pintada.
        return

    # ---------- Identity ----------
    def _on_global_company_changed(self) -> None:
        company_key = str(self.company_combo.currentData() or "vesper")
        if company_key not in COMPANIES:
            company_key = "vesper"
        cfg = self.app_context.state.config
        cfg.default_company_key = company_key
        cfg.smtp_active_profile = company_for_key(company_key).smtp_profile
        try:
            cfg.save()
        except Exception:
            pass
        self._sync_global_identity_to_composer()

    def _on_global_signature_changed(self) -> None:
        owner = str(self.signature_combo.currentText() or "").strip()
        if owner:
            try:
                cfg = self.app_context.state.config
                cfg.last_signature_owner = owner
                cfg.save()
            except Exception:
                pass
        self._sync_global_identity_to_composer()

    def _sync_global_identity_to_composer(self) -> None:
        if self._composer_page is None:
            return
        company_key = str(self.company_combo.currentData() or "vesper")
        owner = str(self.signature_combo.currentText() or "")
        try:
            self._composer_page.set_company_key_public(company_key)
            if owner:
                self._composer_page.set_signature_owner_public(owner)
        except Exception:
            pass

    # ---------- Shortcuts / menus ----------
    def _install_global_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(lambda: self._set_page("new_request"))
        QShortcut(QKeySequence("Ctrl+H"), self).activated.connect(lambda: self._set_page("history"))
        QShortcut(QKeySequence("Ctrl+Alt+A"), self).activated.connect(lambda: self._set_page("settings"))

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "Ajuda rápida",
            "Fluxo rápido:\n\n"
            "1. Nova cotação: escolha Material, Painéis EX, Frete ou OC.\n"
            "2. Fornecedores: busque por produto, empresa ou e-mail.\n"
            "3. Acompanhar: veja quem respondeu e cobre quem falta.\n"
            "4. Configurações: ajuste contas, assinaturas e manutenção.",
        )

    def _open_more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Verificar respostas agora", self._sync_replies_in_background)
        menu.addAction("Atualizar base", lambda: self._reload_suppliers_if_loaded(force=True))
        menu.addAction(f"Tema: {self.theme_manager.mode_label()} — alternar", self._cycle_theme_mode)
        menu.addSeparator()
        admin_menu = menu.addMenu("Administração")
        admin_menu.addAction("Modelos e assinaturas", lambda: self._set_page("models"))
        admin_menu.addAction("Configurações", lambda: self._set_page("settings"))
        menu.exec(self.mapToGlobal(self.rect().topRight()))

    def _reload_suppliers_if_loaded(self, *, force: bool = False) -> None:
        self._ensure_page_loaded("suppliers")
        idx = self._page_indexes.get("suppliers")
        if idx is None:
            return
        page = self.stack.widget(idx)
        if hasattr(page, "_reload_base"):
            try:
                page._reload_base(force_refresh=force)
            except Exception:
                pass

    def _cycle_theme_mode(self) -> None:
        mode = self.theme_manager.cycle_mode()
        try:
            self.theme_manager.repolish()
        except Exception:
            pass
        self.statusBar().showMessage(f"Tema {'escuro' if mode == 'dark' else 'claro'} aplicado.", 3000)

    def _sync_replies_in_background(self) -> None:
        if getattr(self, "_imap_sync_running", False):
            self.statusBar().showMessage("Verificação de respostas já está em andamento.", 3000)
            return
        self._imap_sync_running = True
        self.statusBar().showMessage("Verificando respostas em segundo plano...", 3000)
        try:
            from app.qt.pages.history_page import IMAPSyncRunnable
            runnable = IMAPSyncRunnable(self.app_context.state.config, self.app_context.state.history)
            runnable.signals.done.connect(self._on_background_sync_done)
            runnable.signals.error.connect(self._on_background_sync_error)
            QThreadPool.globalInstance().start(runnable)
        except Exception as exc:
            self._imap_sync_running = False
            self.statusBar().showMessage(f"Não foi possível iniciar a verificação: {exc}", 5000)

    def _on_background_sync_done(self, summary) -> None:
        self._imap_sync_running = False
        try:
            message = summary.message() if hasattr(summary, "message") else "Respostas verificadas."
        except Exception:
            message = "Respostas verificadas."
        self.statusBar().showMessage(message, 5000)
        idx = self._page_indexes.get("history")
        if idx is not None:
            page = self.stack.widget(idx)
            if hasattr(page, "_refresh"):
                try:
                    page._refresh()
                except Exception:
                    pass
    def _on_background_sync_error(self, err_msg: str) -> None:
        self._imap_sync_running = False
        self.statusBar().showMessage("Não foi possível verificar respostas. Veja Configurações se continuar.", 5000)

    def _process_smtp_queue_in_background(self) -> None:
        if getattr(self, "_smtp_queue_running", False):
            return
        self._smtp_queue_running = True
        try:
            from app.core.smtp_queue import SMTPQueueProcessRunnable
            runnable = SMTPQueueProcessRunnable(self.app_context.state.config)
            runnable.signals.done.connect(self._on_smtp_queue_done)
            runnable.signals.error.connect(self._on_smtp_queue_error)
            QThreadPool.globalInstance().start(runnable)
        except Exception:
            self._smtp_queue_running = False

    def _on_smtp_queue_done(self, summary: object) -> None:
        self._smtp_queue_running = False
        try:
            sent = int(getattr(summary, "sent", 0) or 0)
            failed = int(getattr(summary, "failed", 0) or 0)
            if sent or failed:
                self.statusBar().showMessage(f"Fila de envio: {sent} reenviado(s), {failed} ainda pendente(s).", 5000)
        except Exception:
            pass

    def _on_smtp_queue_error(self, _message: str) -> None:
        self._smtp_queue_running = False

    def _reopen_event_in_composer(self, event: dict | None = None) -> None:
        from app.core.dashboard_insights import event_type_label, recipients
        if not isinstance(event, dict):
            self._open_task(REQUEST_MATERIAL)
            return
        label = event_type_label(event).casefold()
        if "frete" in label:
            request_type = REQUEST_FREIGHT
        elif "ordem" in label:
            request_type = REQUEST_PURCHASE_ORDER
        elif "pain" in label or " ex" in f" {label}":
            request_type = REQUEST_EX_PANELS
        else:
            request_type = REQUEST_MATERIAL
        self._open_task(request_type)
        page = self._composer_page
        if page is None:
            return
        try:
            product = str(event.get("product_query") or "").strip()
            if request_type == REQUEST_MATERIAL and hasattr(page, "smart_input") and product:
                page.smart_input.setPlainText(product)
            elif request_type == REQUEST_FREIGHT and hasattr(page, "freight_desc") and product:
                page.freight_desc.setText(product)
            elif request_type == REQUEST_PURCHASE_ORDER and hasattr(page, "po_number"):
                import re
                subject = str(event.get("subject") or event.get("product_query") or "")
                m = re.search(r"(?:OC|ORDEM\s+DE\s+COMPRA)[^0-9]*(\d+)", subject, re.I)
                if m:
                    page.po_number.setText(m.group(1))
            for rec in recipients(event):
                email = str(rec.get("email") or "").strip()
                if email and hasattr(page, "_selected_recipients"):
                    page._selected_recipients[email.lower()] = dict(rec)
            if hasattr(page, "_refresh_all"):
                page._refresh_all()
        except Exception:
            pass


    # ---------- Update flow (preservado) ----------
    def _start_update_flow(self) -> None:
        cfg = self.app_context.state.config
        if not bool(getattr(cfg, "update_enabled", True)):
            return
        if not bool(getattr(cfg, "update_check_on_start", True)):
            return
        if not bool(getattr(cfg, "update_download_silent", True)):
            return
        self._start_update_worker("check_and_download")

    def _start_update_worker(self, mode: str) -> None:
        if self._update_job_running:
            return
        self._update_job_running = True
        signals = UpdateWorkerSignals(self)
        signals.state.connect(self._on_update_state)
        self._update_signals = signals
        worker = UpdateWorker(service=self._update_service, mode=mode, signals=signals)
        self._update_thread_pool.start(worker)

    def _on_update_state(self, state: str, payload: object) -> None:
        state_name = str(state or "idle")
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        self._update_state = state_name
        self._update_payload = payload_dict
        if state_name in {"idle", "ready_to_install", "error_download"}:
            self._update_job_running = False
        self._render_update_banner()
        self._apply_update_state_to_settings_page()

    def _render_update_banner(self) -> None:
        state = self._update_state
        payload = dict(self._update_payload or {})
        latest = str(payload.get("latest_version") or "").strip()
        progress = int(payload.get("progress") or 0)
        message = str(payload.get("message") or "").strip()
        self.update_banner_action.hide()
        self.update_banner_action.setEnabled(True)
        if state == "available_downloading":
            self.update_banner_label.setText(f"Há uma atualização disponível (versão {latest}). Baixando em segundo plano… {progress}%")
            self.update_banner_action.setText("Baixando…")
            self.update_banner_action.setEnabled(False)
            self.update_banner_action.show(); self.update_banner.show(); return
        if state == "ready_to_install":
            self.update_banner_label.setText(f"Atualização pronta para instalar (versão {latest}).")
            self.update_banner_action.setText("Instalar e reiniciar")
            self.update_banner_action.show(); self.update_banner.show(); return
        if state == "error_download":
            text = f"Não foi possível baixar a atualização (versão {latest})."
            if message:
                text = f"{text} Detalhes: {message}"
            self.update_banner_label.setText(text)
            self.update_banner_action.setText("Tentar novamente")
            self.update_banner_action.show(); self.update_banner.show(); return
        if state == "installing":
            self.update_banner_label.setText("Instalando a atualização. O aplicativo será reiniciado em instantes.")
            self.update_banner_action.setText("Aguarde…")
            self.update_banner_action.setEnabled(False)
            self.update_banner_action.show(); self.update_banner.show(); return
        self.update_banner.hide()

    def _apply_update_state_to_settings_page(self) -> None:
        idx = self._page_indexes.get("settings")
        if idx is None:
            return
        page = self.stack.widget(idx)
        if isinstance(page, SettingsPage):
            page.set_update_status(self._update_state, dict(self._update_payload))

    def _handle_update_banner_action(self) -> None:
        if self._update_state == "ready_to_install":
            self._install_update_from_ui()
        elif self._update_state == "error_download":
            self._retry_update_download()

    def _retry_update_download(self) -> None:
        self._start_update_worker("retry_download")

    def _install_update_from_ui(self) -> None:
        installer_path = str(dict(self._update_payload or {}).get("installer_path") or "").strip()
        if not installer_path:
            QMessageBox.warning(self, "Atualização", "Instalador não encontrado para aplicar.")
            return
        ok, message = self._update_service.schedule_install_and_restart(installer_path, current_executable=str(sys.executable))
        if not ok:
            QMessageBox.critical(self, "Atualização", message)
            return
        self._on_update_state("installing", {"message": message})
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        try:
            self.app_context.job_manager.shutdown(wait=False)
        except Exception:
            pass
        super().closeEvent(event)
