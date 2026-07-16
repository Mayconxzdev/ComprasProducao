from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import html as html_lib
import re
import uuid
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, QSize, QSignalBlocker, QRect
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QResizeEvent, QColor, QFont, QFontMetrics, QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QStyledItemDelegate,
    QStyle,
    QInputDialog,
    QMenu,
)

from app.application.context import AppContext
from app.core.companies import COMPANIES, company_for_key
from app.core.contact_index import build_contact_index
from app.core.local_supplier_db import connect_local_db, get_all_local_suppliers
from app.core.thunderbird_contacts import load_cached_thunderbird_contacts
from app.core.email_signature import (
    build_html_email_body,
    first_signature_owner,
    load_signature_html,
    resolve_signature_html_path,
    signature_owner_options,
)
from app.core.email_templates import (
    DEFAULT_FREIGHT_CARRIERS,
    build_freight_email,
    build_material_email,
    build_purchase_order_email,
    clean_text,
    dedupe_emails,
    is_valid_email,
    parse_freight_fields,
    summarize_subject,
)
from app.core.ex_panels_library import add_ex_panel_to_library, load_ex_panel_library, save_ex_panel_library
from app.core.signature_identity import current_signature_identity, resolve_signature_owner
from app.core.smart_parser import (
    REQUEST_FREIGHT,
    REQUEST_MATERIAL,
    REQUEST_PURCHASE_ORDER,
    SmartAnalysis,
    analyze_smart_input,
    strip_email_only_lines,
    validate_attachment_path,
)
from app.core.smtp_handler import get_password_from_profile, send_email_with_profile
from app.core.smtp_queue import enqueue_email
from app.core.utils_text import normalize_text
from app.core.recipient_search import search_recipient_rows, recipient_match_score, dedupe_recipient_rows
from app.qt.services.supplier_edit_service import SupplierEditService
from app.qt.ui_scale import font_css, scaled_px

REQUEST_PO = REQUEST_PURCHASE_ORDER
REQUEST_EX_PANELS = "ex_panels"
REQUEST_SUPPLIERS = "suppliers"


class _SendSignals(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    error = Signal(str)


class _SendRunnable(QRunnable):
    def __init__(
        self,
        *,
        app_context: AppContext,
        recipients: list[str],
        subject: str,
        body: str,
        body_html: str,
        password: str,
        attachments: list[str],
        signals: _SendSignals,
        tracking_id: str | None = None,
    ) -> None:
        super().__init__()
        self.app_context = app_context
        self.recipients = recipients
        self.subject = subject
        self.body = body
        self.body_html = body_html
        self.password = password
        self.attachments = attachments
        self.signals = signals
        self.tracking_id = tracking_id

    def run(self) -> None:
        def _on_progress(done, total, recipient):
            try:
                self.signals.progress.emit(done, total, recipient)
            except RuntimeError:
                pass

        try:
            result = send_email_with_profile(
                self.app_context.state.config,
                self.recipients,
                self.subject,
                self.body,
                body_html=self.body_html,
                attachments=self.attachments,
                password=self.password,
                on_progress=_on_progress,
                tracking_id=self.tracking_id,
            )
            try:
                self.signals.done.emit(result)
            except RuntimeError:
                pass
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass


class SmartInputBox(QTextEdit):
    """Text box that also accepts files dropped/pasted from Windows."""

    filesDropped = Signal(list)
    smartTextDropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("smartInput")

    def _paths_from_mime(self, mime: object) -> list[str]:
        if not hasattr(mime, "hasUrls") or not mime.hasUrls():  # type: ignore[attr-defined]
            return []
        paths: list[str] = []
        for url in mime.urls():  # type: ignore[attr-defined]
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        return paths

    def canInsertFromMimeData(self, source: object) -> bool:  # noqa: N802 - Qt API
        if self._paths_from_mime(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: object) -> None:  # noqa: N802 - Qt API
        paths = self._paths_from_mime(source)
        if paths:
            self.filesDropped.emit(paths)
            return
        if hasattr(source, "hasText") and source.hasText():  # type: ignore[attr-defined]
            self.smartTextDropped.emit(source.text())  # type: ignore[attr-defined]
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        mime = event.mimeData()  # type: ignore[attr-defined]
        if self._paths_from_mime(mime) or mime.hasText():
            event.acceptProposedAction()  # type: ignore[attr-defined]
            return
        super().dragEnterEvent(event)  # type: ignore[arg-type]

    def dropEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        mime = event.mimeData()  # type: ignore[attr-defined]
        paths = self._paths_from_mime(mime)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()  # type: ignore[attr-defined]
            return
        super().dropEvent(event)  # type: ignore[arg-type]



class _RecipientBaseSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class _RecipientBaseLoadRunnable(QRunnable):
    """Carrega a base de fornecedores para a tela Nova cotação sem depender da tela Fornecedores.

    A busca de destinatários precisa funcionar assim que o usuário digita. Antes, a base
    só ficava realmente quente depois de abrir Fornecedores ou após alguns segundos,
    criando o efeito de "do nada começa a achar".
    """

    def __init__(self, *, app_context: AppContext, signals: _RecipientBaseSignals) -> None:
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



class RecipientRowFrame(QFrame):
    """Linha clicável inteira para destinatários.

    A checkbox é apenas indicador visual. O clique no card, nome, e-mail ou
    espaço vazio alterna o mesmo estado, evitando o erro operacional de ter que
    acertar exatamente a caixinha.
    """

    clicked = Signal(str, object)

    def __init__(self, *, email: str, row: dict[str, str], checked: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.email = email
        self.row = dict(row)
        self.setObjectName("recipientRow")
        self.setProperty("checked", "true" if checked else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        try:
            if event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
                self.clicked.emit(self.email, self.row)
                event.accept()  # type: ignore[attr-defined]
                return
        except Exception:
            pass
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]

    def keyPressEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        try:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):  # type: ignore[attr-defined]
                self.clicked.emit(self.email, self.row)
                event.accept()  # type: ignore[attr-defined]
                return
        except Exception:
            pass
        super().keyPressEvent(event)  # type: ignore[arg-type]




class FreightRecipientDelegate(QStyledItemDelegate):
    """Desenha transportadoras no mesmo padrão dos cards de destinatário.

    O Frete não usa setItemWidget em lote porque isso causava uma janela
    órfã/piscada no Windows. Este delegate mantém o visual de card selecionável
    sem criar QWidget por linha.
    """

    ROLE_FREIGHT_CARD = int(Qt.ItemDataRole.UserRole) + 101

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        if not bool(index.data(self.ROLE_FREIGHT_CARD)):
            super().paint(painter, option, index)
            return

        row = index.data(Qt.ItemDataRole.UserRole) or {}
        if not isinstance(row, dict):
            row = {}
        company = clean_text(row.get("empresa")) or "Transportadora"
        email = clean_text(row.get("email"))
        contact = clean_text(row.get("contato_nome"))
        product = clean_text(row.get("produto") or row.get("kind_label") or row.get("kind"))
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

        palette = option.palette
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if dark:
            bg = QColor("#16304D" if checked else "#152033")
            if hovered or selected:
                bg = QColor("#1B4269" if checked else "#1A2A42")
            border = QColor("#5AA7FF" if checked else "#30445F")
            text = QColor("#F7FAFC")
            muted = QColor("#C4D0DF")
            check_bg = QColor("#5AA7FF" if checked else "#101927")
            check_border = QColor("#86C5FF" if checked else "#58708F")
            remove = QColor("#D9E8FA")
        else:
            bg = QColor("#EAF3FE" if checked else "#FFFFFF")
            if hovered or selected:
                bg = QColor("#DDEEFF" if checked else "#F9FBFF")
            border = QColor("#1268B3" if checked else "#D7E1EE")
            text = QColor("#07142B")
            muted = QColor("#5F7088")
            check_bg = QColor("#1268B3" if checked else "#FFFFFF")
            check_border = QColor("#1268B3" if checked else "#8EA9C8")
            remove = QColor("#37506D")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect.adjusted(scaled_px(6), scaled_px(5), -scaled_px(6), -scaled_px(5))
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, scaled_px(12), scaled_px(12))

        box_size = scaled_px(20)
        box_rect = QRect(rect.left() + scaled_px(12), rect.top() + (rect.height() - box_size) // 2, box_size, box_size)
        painter.setPen(QPen(check_border, 1.2))
        painter.setBrush(check_bg)
        painter.drawRoundedRect(box_rect, scaled_px(5), scaled_px(5))
        if checked:
            painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
            painter.drawLine(box_rect.left() + scaled_px(5), box_rect.center().y(), box_rect.left() + scaled_px(9), box_rect.bottom() - scaled_px(5))
            painter.drawLine(box_rect.left() + scaled_px(9), box_rect.bottom() - scaled_px(5), box_rect.right() - scaled_px(4), box_rect.top() + scaled_px(5))

        text_left = box_rect.right() + scaled_px(14)
        remove_width = scaled_px(28) if checked else 0
        text_width = max(20, rect.right() - text_left - scaled_px(12) - remove_width)
        name_rect = QRect(text_left, rect.top() + scaled_px(12), text_width, scaled_px(18))
        meta_rect = QRect(text_left, rect.top() + scaled_px(32), text_width, scaled_px(18))

        name_font = QFont(option.font)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(text)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, QFontMetrics(name_font).elidedText(company, Qt.TextElideMode.ElideRight, name_rect.width()))

        meta_parts = [part for part in [contact, email, product] if part]
        meta = "  •  ".join(meta_parts) if meta_parts else email
        meta_font = QFont(option.font)
        meta_font.setPointSize(max(8, option.font.pointSize() - 1))
        painter.setFont(meta_font)
        painter.setPen(muted)
        painter.drawText(meta_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, QFontMetrics(meta_font).elidedText(meta, Qt.TextElideMode.ElideRight, meta_rect.width()))

        if checked:
            x_rect = QRect(rect.right() - scaled_px(34), rect.top() + scaled_px(16), scaled_px(22), scaled_px(22))
            x_font = QFont(option.font)
            x_font.setBold(True)
            painter.setFont(x_font)
            painter.setPen(remove)
            painter.drawText(x_rect, Qt.AlignmentFlag.AlignCenter, "×")

        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        if bool(index.data(self.ROLE_FREIGHT_CARD)):
            return QSize(10, scaled_px(70))
        return super().sizeHint(option, index)

class AttachmentDropZone(QFrame):
    filesDropped = Signal(list)
    pickRequested = Signal()
    pasteRequested = Signal()
    removeRequested = Signal(str)

    def __init__(self, *, title: str = "Anexos", hint: str = "Arraste arquivos aqui ou clique para escolher", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_text = title
        box = QVBoxLayout(self)
        box.setContentsMargins(scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14))
        box.setSpacing(scaled_px(7))
        top = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setStyleSheet(font_css(15, 800))
        top.addWidget(self.title, 0)
        top.addStretch(1)
        self.btn_pick = QPushButton("Selecionar arquivos")
        self.btn_pick.setObjectName("secondarySmall")
        self.btn_pick.clicked.connect(self.pickRequested.emit)
        top.addWidget(self.btn_pick, 0)
        box.addLayout(top)
        self.hint = QLabel(hint + " • Ctrl+V")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.files_label = QLabel("Nenhum anexo")
        self.files_label.setObjectName("attachmentFiles")
        self.files_label.setWordWrap(True)
        self.files_list = QListWidget(self)
        self.files_list.setObjectName("attachmentList")
        self.files_list.setSpacing(scaled_px(3))
        self.files_list.setMaximumHeight(scaled_px(104))
        box.addWidget(self.hint)
        box.addWidget(self.files_label)
        box.addWidget(self.files_list)

    def set_title(self, title: str) -> None:
        self._title_text = title
        self.title.setText(title)

    def set_hint(self, hint: str) -> None:
        self.hint.setText(hint + " • Ctrl+V")

    def set_files(self, files: list[str]) -> None:
        self.files_list.clear()
        if not files:
            self.files_label.setText("Nenhum anexo")
            self.files_list.hide()
            return
        names = [Path(path).name for path in files]
        self.files_label.setText(f"{len(names)} anexo(s)")
        self.files_list.show()
        for path, name in zip(files, names):
            item = QListWidgetItem()
            item.setSizeHint(QSize(10, scaled_px(34)))
            row = QFrame(self.files_list)
            row.setObjectName("attachmentRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(scaled_px(8), scaled_px(3), scaled_px(6), scaled_px(3))
            layout.setSpacing(scaled_px(8))
            label = QLabel(name)
            label.setObjectName("attachmentName")
            label.setToolTip(path)
            layout.addWidget(label, 1)
            btn = QPushButton("×")
            btn.setObjectName("iconRemove")
            btn.setToolTip("Remover anexo")
            btn.setFixedWidth(scaled_px(28))
            btn.clicked.connect(lambda _=False, p=path: self.removeRequested.emit(p))
            layout.addWidget(btn, 0)
            self.files_list.addItem(item)
            self.files_list.setItemWidget(item, row)

    def mousePressEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        self.pickRequested.emit()
        super().mousePressEvent(event)  # type: ignore[arg-type]

    def _paths_from_mime(self, mime: object) -> list[str]:
        if not hasattr(mime, "hasUrls") or not mime.hasUrls():  # type: ignore[attr-defined]
            return []
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]  # type: ignore[attr-defined]

    def dragEnterEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        if self._paths_from_mime(event.mimeData()):  # type: ignore[attr-defined]
            event.acceptProposedAction()  # type: ignore[attr-defined]
            return
        super().dragEnterEvent(event)  # type: ignore[arg-type]

    def dropEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        paths = self._paths_from_mime(event.mimeData())  # type: ignore[attr-defined]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()  # type: ignore[attr-defined]
            return
        super().dropEvent(event)  # type: ignore[arg-type]


class NewRequestPage(QWidget):
    """Tarefa única, visual final: Material, Frete e OC com destinatários sempre claros."""

    def __init__(
        self,
        app_context: AppContext,
        *,
        on_status: Callable[[str], None] | None = None,
        on_open_suppliers: Callable[[], None] | None = None,
        on_open_history: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_cycle_theme: Callable[[], None] | None = None,
        embedded_shell: bool = False,
        initial_request_type: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_context = app_context
        self._on_status = on_status
        self._on_open_suppliers = on_open_suppliers
        self._on_open_history = on_open_history
        self._on_open_settings = on_open_settings
        self._on_cycle_theme = on_cycle_theme
        self._embedded_shell = bool(embedded_shell)
        self._initial_request_type = initial_request_type
        self._thread_pool = QThreadPool.globalInstance()
        self._supplier_edit_service = SupplierEditService(self.app_context)
        try:
            self._thunderbird_contacts = load_cached_thunderbird_contacts()
        except Exception:
            self._thunderbird_contacts = []

        valid_initial_types = {REQUEST_SUPPLIERS, REQUEST_MATERIAL, REQUEST_EX_PANELS, REQUEST_FREIGHT, REQUEST_PO}
        self._request_type = initial_request_type if initial_request_type in valid_initial_types else REQUEST_MATERIAL
        self._is_initializing = True
        self._custom_quote_type: dict[str, Any] | None = None
        self._custom_field_widgets: dict[str, QWidget] = {}
        self._request_type_was_manual = False
        self._company_key = clean_text(getattr(self.app_context.state.config, "default_company_key", "vesper")) or "vesper"
        if self._company_key not in COMPANIES:
            self._company_key = "vesper"
        self._selected_recipients: dict[str, dict[str, str]] = {}
        self._extra_carriers: dict[str, dict[str, str]] = {}
        self._carrier_selected: set[str] = set()
        self._carrier_hidden_session: set[str] = set()
        self._attachments: list[str] = []
        self._signature_cache: dict[str, str] = {}
        self._sending = False
        self._last_analysis = SmartAnalysis()
        self._auto_emails_added: set[str] = set()
        self._updating_freight_fields = False
        self._compact_layout = False
        self._ex_panels: list[dict[str, Any]] = []
        self._ex_panel_library: list[dict[str, Any]] = load_ex_panel_library()
        self._editing_ex_panel_index: int | None = None
        self._editing_ex_template_index: int | None = None
        self._recipient_base_loading = False
        self._recipient_base_loaded_once = False
        self._recipient_base_signals: _RecipientBaseSignals | None = None
        self._recipient_candidates_cache: list[dict[str, str]] | None = None
        self._recipient_cache_generation = 0
        self._recipient_visible_signature: tuple[tuple[str, bool], ...] = tuple()
        self._drop_zone_files_state: tuple[str, ...] | None = None

        self._analysis_timer = QTimer(self)
        # Análise do material é deliberadamente mais calma: digitação nunca deve
        # travar, roubar foco nem redesenhar o painel de destinatários a cada tecla.
        self._analysis_timer.setInterval(520)
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.timeout.connect(self._analyze_and_refresh)
        self._suggest_timer = QTimer(self)
        # Pesquisa de destinatários usa ranking fuzzy e pode consultar centenas de contatos.
        # Debounce um pouco maior elimina travas ao digitar sem parecer lento.
        self._suggest_timer.setInterval(280)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.timeout.connect(self._refresh_supplier_suggestions)
        self._freight_refresh_timer = QTimer(self)
        self._freight_refresh_timer.setInterval(180)
        self._freight_refresh_timer.setSingleShot(True)
        self._freight_refresh_timer.timeout.connect(self._refresh_all)

        self._build_ui()
        self._apply_initial_identity()
        self._set_company(self._company_key)
        self.import_selected_emails_from_state()
        self._install_paste_shortcut()
        self._setup_completer()
        self._is_initializing = False
        self._set_request_type(self._initial_request_type or self._request_type or REQUEST_SUPPLIERS, manual=False)
        self._analyze_and_refresh()
        QTimer.singleShot(0, self._ensure_recipient_base_loaded_async)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(scaled_px(10))

        self.internal_header = self._build_header()
        self.identity_bar = self._build_identity_bar()
        if self._embedded_shell:
            self.internal_header.hide()
            self.identity_bar.hide()
            self.page_intro = QFrame(self)
            self.page_intro.setObjectName("embeddedIntro")
            intro_box = QVBoxLayout(self.page_intro)
            intro_box.setContentsMargins(scaled_px(8), 0, scaled_px(8), scaled_px(2))
            intro_box.setSpacing(scaled_px(2))
            self.embedded_title = QLabel("Nova cotação")
            self.embedded_title.setObjectName("pageTitle")
            self.embedded_subtitle = QLabel("Preencha apenas o necessário e escolha quem receberá o envio.")
            self.embedded_subtitle.setObjectName("pageSubtitle")
            intro_box.addWidget(self.embedded_title)
            intro_box.addWidget(self.embedded_subtitle)
            root.addWidget(self.page_intro, 0)
        else:
            root.addWidget(self.internal_header, 0)
            root.addWidget(self.identity_bar, 0)

        self.content = QFrame(self)
        self.content.setObjectName("finalContent")
        self.content_grid = QGridLayout(self.content)
        self.content_grid.setContentsMargins(0, 0, 0, 0)
        self.content_grid.setHorizontalSpacing(scaled_px(14))
        self.content_grid.setVerticalSpacing(scaled_px(12))

        self.task_stack = QStackedWidget(self.content)
        self.task_stack.setObjectName("taskStack")
        self.task_stack.addWidget(self._build_suppliers_task())
        self.task_stack.addWidget(self._build_material_task())
        self.task_stack.addWidget(self._build_ex_panels_task())
        self.task_stack.addWidget(self._build_freight_task())
        self.task_stack.addWidget(self._build_po_task())
        self.recipients_panel = self._build_recipients_panel()
        self._apply_responsive_layout(force=True)
        root.addWidget(self.content, 1)

        self.send_bar = self._build_send_bar()
        if self._embedded_shell and hasattr(self, "btn_clear"):
            self.btn_clear.hide()
        root.addWidget(self.send_bar, 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self, *, force: bool = False) -> None:
        if not hasattr(self, "content_grid"):
            return
        width = max(0, self.width())
        compact = width < scaled_px(1120)
        if not force and compact == self._compact_layout:
            return
        self._compact_layout = compact

        self.content_grid.removeWidget(self.task_stack)
        self.content_grid.removeWidget(self.recipients_panel)
        suppliers_mode = self._request_type == REQUEST_SUPPLIERS
        self.recipients_panel.setVisible(not suppliers_mode)
        if hasattr(self, "identity_bar"):
            self.identity_bar.setVisible((not suppliers_mode) and (not getattr(self, "_embedded_shell", False)))
        if hasattr(self, "send_bar"):
            self.send_bar.setVisible(not suppliers_mode)
        if suppliers_mode:
            self.content_grid.addWidget(self.task_stack, 0, 0, 1, 2)
            self.content_grid.setColumnStretch(0, 1)
            self.content_grid.setColumnStretch(1, 0)
            self.content_grid.setRowStretch(0, 1)
            self.content_grid.setRowStretch(1, 0)
            self.btn_recents.setVisible(True)
            self.btn_help.setVisible(True)
            return
        if compact:
            self.content_grid.addWidget(self.task_stack, 0, 0)
            self.content_grid.addWidget(self.recipients_panel, 1, 0)
            self.content_grid.setColumnStretch(0, 1)
            self.content_grid.setColumnStretch(1, 0)
            self.content_grid.setRowStretch(0, 7)
            self.content_grid.setRowStretch(1, 6)
            self.supplier_results.setMinimumHeight(scaled_px(170))
            self.smart_input.setMinimumHeight(scaled_px(155))
            self.btn_recents.setVisible(False)
            self.btn_help.setVisible(False)
        else:
            self.content_grid.addWidget(self.task_stack, 0, 0)
            self.content_grid.addWidget(self.recipients_panel, 0, 1)
            self.content_grid.setColumnStretch(0, 11)
            self.content_grid.setColumnStretch(1, 10)
            self.content_grid.setRowStretch(0, 1)
            self.content_grid.setRowStretch(1, 0)
            self.supplier_results.setMinimumHeight(scaled_px(260))
            self.smart_input.setMinimumHeight(scaled_px(245))
            self.btn_recents.setVisible(True)
            self.btn_help.setVisible(True)

    def _build_header(self) -> QFrame:
        header = QFrame(self)
        header.setObjectName("finalHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(scaled_px(16), scaled_px(10), scaled_px(16), scaled_px(10))
        row.setSpacing(scaled_px(12))
        logo = QLabel("V")
        logo.setObjectName("brandLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(logo, 0)
        title = QLabel("Compras Vesper")
        title.setStyleSheet(font_css(22, 800))
        row.addWidget(title, 0)
        row.addStretch(1)
        self.type_buttons: dict[str, QPushButton] = {}
        for key, text in (
            (REQUEST_SUPPLIERS, "Fornecedores"),
            (REQUEST_MATERIAL, "Material"),
            (REQUEST_EX_PANELS, "Painéis EX"),
            (REQUEST_FREIGHT, "Frete"),
            (REQUEST_PO, "Ordem de compra"),
        ):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName("segmentButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumWidth(scaled_px(126 if key not in {REQUEST_PO, REQUEST_SUPPLIERS} else 156))
            btn.clicked.connect(lambda _=False, t=key: self._set_request_type(t, manual=True))
            self.type_buttons[key] = btn
            row.addWidget(btn, 0)
        row.addStretch(1)
        self.btn_recents = QPushButton("Recentes")
        self.btn_recents.setObjectName("secondarySmall")
        self.btn_recents.clicked.connect(self._open_history_area)
        row.addWidget(self.btn_recents, 0)
        self.btn_help = QPushButton("Ajuda")
        self.btn_help.setObjectName("secondarySmall")
        self.btn_help.clicked.connect(self._show_quick_help)
        row.addWidget(self.btn_help, 0)
        self.btn_more = QPushButton("⋯")
        self.btn_more.setObjectName("secondarySmall")
        self.btn_more.setMinimumWidth(scaled_px(42))
        self.btn_more.clicked.connect(self._open_more_menu)
        row.addWidget(self.btn_more, 0)
        return header

    def _build_identity_bar(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("identityBar")
        row = QHBoxLayout(frame)
        row.setContentsMargins(scaled_px(16), scaled_px(9), scaled_px(16), scaled_px(9))
        row.setSpacing(scaled_px(12))
        row.addWidget(QLabel("Enviar como:"), 0)
        self.btn_company_vesper = QPushButton("Vesper")
        self.btn_company_ventrio = QPushButton("Vent Rio")
        for btn in (self.btn_company_vesper, self.btn_company_ventrio):
            btn.setCheckable(True)
            btn.setObjectName("companyPill")
            btn.setMinimumWidth(scaled_px(96))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_company_vesper.clicked.connect(lambda: self._set_company("vesper"))
        self.btn_company_ventrio.clicked.connect(lambda: self._set_company("ventrio"))
        row.addWidget(self.btn_company_vesper, 0)
        row.addWidget(self.btn_company_ventrio, 0)
        row.addSpacing(scaled_px(12))
        row.addWidget(QLabel("Assinatura:"), 0)
        self.signature_combo = QComboBox(frame)
        self.signature_combo.setMinimumWidth(scaled_px(230))
        self.signature_combo.currentTextChanged.connect(lambda *_: self._refresh_all())
        row.addWidget(self.signature_combo, 0)
        self.identity_hint = QLabel("")
        self.identity_hint.setObjectName("muted")
        self.identity_hint.setWordWrap(True)
        row.addWidget(self.identity_hint, 1)
        self.btn_save_signature_map = QPushButton("Usar sempre neste PC")
        self.btn_save_signature_map.setObjectName("secondarySmall")
        self.btn_save_signature_map.clicked.connect(self._save_signature_map_for_identity)
        row.addWidget(self.btn_save_signature_map, 0)
        return frame

    def _card(self, parent: QWidget, title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
        card = QFrame(parent)
        card.setObjectName("finalCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14))
        box.setSpacing(scaled_px(10))
        top = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("cardTitle")
        top.addWidget(label, 0)
        top.addStretch(1)
        box.addLayout(top)
        if hint:
            sub = QLabel(hint)
            sub.setObjectName("muted")
            sub.setWordWrap(True)
            box.addWidget(sub, 0)
        return card, box, top

    def _build_suppliers_task(self) -> QWidget:
        # O composer não deve carregar a base de fornecedores inteira ao abrir.
        # A tela principal Fornecedores continua sendo o buscador oficial e carrega
        # a planilha em worker somente quando o usuário realmente entra nela.
        page = QFrame(self)
        page.setObjectName("modePage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(scaled_px(12))
        card, layout, _top = self._card(page, "Fornecedores", "Busque por produto, empresa ou e-mail na tela dedicada e volte com os destinatários selecionados.")
        btn = QPushButton("Abrir fornecedores")
        btn.setObjectName("accent")
        btn.clicked.connect(lambda: self._on_open_suppliers() if self._on_open_suppliers else None)
        layout.addWidget(btn, 0)
        layout.addStretch(1)
        box.addWidget(card, 0)
        box.addStretch(1)
        self.suppliers_page = None
        return page

    def _build_material_task(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("modePage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(scaled_px(12))
        card, layout, top = self._card(page, "Material para cotar", "Cole ou digite os itens exatamente como quer que o fornecedor receba.")
        self.material_title_label = top.itemAt(0).widget() if top.count() else None
        self.material_hint_label = layout.itemAt(1).widget() if layout.count() > 1 else None
        self.ex_required_check = QCheckBox("Produto Ex / pedir certificado")
        self.ex_required_check.setObjectName("inlineCheck")
        self.ex_required_check.stateChanged.connect(lambda *_: self._refresh_all())
        top.addWidget(self.ex_required_check, 0)
        self.custom_fields_card = QFrame(card)
        self.custom_fields_card.setObjectName("customFieldsCard")
        self.custom_fields_grid = QGridLayout(self.custom_fields_card)
        self.custom_fields_grid.setContentsMargins(scaled_px(2), scaled_px(2), scaled_px(2), scaled_px(2))
        self.custom_fields_grid.setHorizontalSpacing(scaled_px(12))
        self.custom_fields_grid.setVerticalSpacing(scaled_px(10))
        self.custom_fields_card.hide()
        layout.addWidget(self.custom_fields_card, 1)
        self.smart_input = SmartInputBox(card)
        self.smart_input.setPlaceholderText("Cole ou digite o material aqui...")
        self.smart_input.setMinimumHeight(scaled_px(245))
        self.smart_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.smart_input.textChanged.connect(self._schedule_analysis)
        self.smart_input.filesDropped.connect(self._add_attachments)
        layout.addWidget(self.smart_input, 1)
        box.addWidget(card, 1)
        self.material_drop_zone = AttachmentDropZone(title="Anexos", hint="Arraste arquivos aqui ou clique para escolher", parent=page)
        self.material_drop_zone.filesDropped.connect(self._add_attachments)
        self.material_drop_zone.pickRequested.connect(self._pick_attachments)
        self.material_drop_zone.pasteRequested.connect(self._paste_from_clipboard)
        self.material_drop_zone.removeRequested.connect(self._remove_attachment)
        box.addWidget(self.material_drop_zone, 0)
        return page

    def _build_ex_panels_task(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("modePage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("modeScroll")
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content = QFrame(scroll)
        content.setObjectName("modePage")
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, scaled_px(4), 0)
        box.setSpacing(scaled_px(12))

        self.ex_templates_card, templates_layout, templates_top = self._card(content, "Modelos de painéis", "Selecione um modelo pronto ou cadastre um novo painel abaixo.")
        self.btn_new_ex_panel = QPushButton("Painel novo")
        self.btn_new_ex_panel.setObjectName("secondarySmall")
        self.btn_new_ex_panel.clicked.connect(self._start_new_ex_panel)
        templates_top.addWidget(self.btn_new_ex_panel, 0)
        self.ex_templates_list = QListWidget(self.ex_templates_card)
        self.ex_templates_list.setObjectName("exTemplateList")
        self.ex_templates_list.setSpacing(scaled_px(5))
        self.ex_templates_list.setMinimumHeight(scaled_px(145))
        templates_layout.addWidget(self.ex_templates_list, 1)
        box.addWidget(self.ex_templates_card, 0)

        card, layout, _ = self._card(content, "Painéis EX", "Cadastre cada painel e suas especificações. O e-mail será montado automaticamente.")
        self.ex_form_card = card
        card.setMinimumHeight(scaled_px(265))
        layout.addWidget(QLabel("Nome do painel"), 0)
        self.ex_panel_name = QLineEdit(card)
        self.ex_panel_name.textChanged.connect(lambda *_: self._refresh_all())
        layout.addWidget(self.ex_panel_name, 0)

        self.ex_specs_grid = QGridLayout()
        self.ex_specs_grid.setHorizontalSpacing(scaled_px(14))
        self.ex_specs_grid.setVerticalSpacing(scaled_px(10))
        self.ex_spec_fields: list[QLineEdit] = []
        self.ex_spec_rows: list[dict[str, QWidget]] = []
        for _idx in range(6):
            self._add_ex_spec_field()
        layout.addLayout(self.ex_specs_grid)

        actions = QHBoxLayout()
        self.btn_add_ex_spec = QPushButton("Adicionar especificação")
        self.btn_add_ex_spec.setObjectName("secondarySmall")
        self.btn_add_ex_spec.clicked.connect(lambda: self._add_ex_spec_field(refresh=True))
        actions.addWidget(self.btn_add_ex_spec, 0)
        actions.addStretch(1)
        self.btn_cancel_ex_edit = QPushButton("Cancelar edição")
        self.btn_cancel_ex_edit.setObjectName("secondarySmall")
        self.btn_cancel_ex_edit.clicked.connect(self._cancel_ex_panel_edit)
        self.btn_cancel_ex_edit.setVisible(True)
        actions.addWidget(self.btn_cancel_ex_edit, 0)
        self.btn_save_ex_panel = QPushButton("Concluir painel")
        self.btn_save_ex_panel.setObjectName("accent")
        self.btn_save_ex_panel.clicked.connect(self._save_ex_panel)
        actions.addWidget(self.btn_save_ex_panel, 0)
        layout.addLayout(actions)
        box.addWidget(card, 0)
        self.ex_form_card.hide()

        list_card, list_layout, _top = self._card(content, "Painéis cadastrados", "Edite ou remova os painéis antes de enviar.")
        self.ex_panels_list = QListWidget(list_card)
        self.ex_panels_list.setObjectName("exPanelList")
        self.ex_panels_list.setSpacing(scaled_px(5))
        self.ex_panels_list.setMinimumHeight(scaled_px(150))
        list_layout.addWidget(self.ex_panels_list, 1)
        self.ex_panels_empty = QLabel("Nenhum painel selecionado\nClique em 'Usar' em um modelo para adicionar.")
        self.ex_panels_empty.setObjectName("muted")
        list_layout.addWidget(self.ex_panels_empty, 0)
        box.addWidget(list_card, 1)

        self.ex_drop_zone = AttachmentDropZone(title="Anexos", hint="Arquivos relacionados aos painéis, se houver", parent=content)
        self.ex_drop_zone.filesDropped.connect(self._add_attachments)
        self.ex_drop_zone.pickRequested.connect(self._pick_attachments)
        self.ex_drop_zone.pasteRequested.connect(self._paste_from_clipboard)
        self.ex_drop_zone.removeRequested.connect(self._remove_attachment)
        box.addWidget(self.ex_drop_zone, 0)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self._refresh_ex_templates_list()
        return page

    def _build_freight_task(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("modePage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(scaled_px(12))
        card, layout, _ = self._card(page, "Dados do material", "Preencha os dados que serão enviados às transportadoras.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(scaled_px(14))
        grid.setVerticalSpacing(scaled_px(12))
        self.freight_desc = QLineEdit(card)
        self.freight_volumes = QLineEdit(card)
        self.freight_weight = QLineEdit(card)
        self.freight_nf_value = QLineEdit(card)
        self.freight_measures = QLineEdit(card)
        self.freight_destination = QLineEdit(card)
        self.freight_desc.setPlaceholderText("Ex.: FLANGES")
        self.freight_volumes.setPlaceholderText("Ex.: 02 VOLUMES")
        self.freight_weight.setPlaceholderText("Ex.: 100 kg")
        self.freight_nf_value.setPlaceholderText("Ex.: R$ 6.840,00")
        self.freight_measures.setPlaceholderText("Ex.: 133 x 48 x 48 cm")
        self.freight_destination.setPlaceholderText("Opcional")
        freight_fields = [
            ("Material", self.freight_desc),
            ("Volumes", self.freight_volumes),
            ("Peso", self.freight_weight),
            ("Valor NF", self.freight_nf_value),
            ("Medidas", self.freight_measures),
            ("Observação", self.freight_destination),
        ]
        for idx, (label, widget) in enumerate(freight_fields):
            r = idx // 2
            c = (idx % 2) * 2
            grid.addWidget(QLabel(label), r, c)
            grid.addWidget(widget, r, c + 1)
            widget.textChanged.connect(lambda *_: self._schedule_freight_refresh())
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        box.addWidget(card, 1)
        self.freight_drop_zone = AttachmentDropZone(title="Anexos", hint="Arquivos relacionados ao frete, se houver", parent=page)
        self.freight_drop_zone.filesDropped.connect(self._add_attachments)
        self.freight_drop_zone.pickRequested.connect(self._pick_attachments)
        self.freight_drop_zone.pasteRequested.connect(self._paste_from_clipboard)
        self.freight_drop_zone.removeRequested.connect(self._remove_attachment)
        box.addWidget(self.freight_drop_zone, 0)
        return page

    def _build_po_task(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("modePage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(scaled_px(12))
        card, layout, _ = self._card(page, "Ordem de compra", "Informe a OC, arraste o anexo e escolha quem receberá.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(scaled_px(14))
        grid.setVerticalSpacing(scaled_px(10))
        self.po_number = QLineEdit(card)
        self.po_number.setPlaceholderText("Ex.: 5614")
        self.po_number.textChanged.connect(lambda *_: self._refresh_all())
        self.po_supplier_hint = QLineEdit(card)
        self.po_supplier_hint.setPlaceholderText("Opcional: observação que entrará no e-mail")
        self.po_supplier_hint.textChanged.connect(lambda *_: self._refresh_all())
        grid.addWidget(QLabel("OC"), 0, 0)
        grid.addWidget(self.po_number, 0, 1)
        grid.addWidget(QLabel("Observação"), 1, 0)
        grid.addWidget(self.po_supplier_hint, 1, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.po_drop_zone = AttachmentDropZone(title="Anexo da OC", hint="Arraste o arquivo aqui, clique para selecionar ou use Ctrl+V", parent=card)
        self.po_drop_zone.setObjectName("dropZonePrimary")
        self.po_drop_zone.filesDropped.connect(self._add_attachments)
        self.po_drop_zone.pickRequested.connect(self._pick_attachments)
        self.po_drop_zone.pasteRequested.connect(self._paste_from_clipboard)
        self.po_drop_zone.removeRequested.connect(self._remove_attachment)
        layout.addWidget(self.po_drop_zone, 1)
        box.addWidget(card, 1)
        return page

    def _build_recipients_panel(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("finalCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14))
        box.setSpacing(scaled_px(10))
        header = QHBoxLayout()
        self.recipients_title = QLabel("Destinatários")
        self.recipients_title.setObjectName("cardTitle")
        header.addWidget(self.recipients_title, 0)
        header.addStretch(1)
        box.addLayout(header)
        self.recipients_hint = QLabel("Selecione os destinatários que receberão este envio.")
        self.recipients_hint.setObjectName("muted")
        box.addWidget(self.recipients_hint, 0)
        search = QHBoxLayout()
        self.quick_search = QLineEdit(frame)
        self.quick_search.setPlaceholderText("Buscar fornecedor, produto ou e-mail")
        self.quick_search.textChanged.connect(self._schedule_suggestions)
        self.quick_search.returnPressed.connect(self._add_manual_email)
        search.addWidget(self.quick_search, 1)
        self.btn_add_manual = QPushButton("Adicionar")
        self.btn_add_manual.setObjectName("secondarySmall")
        self.btn_add_manual.clicked.connect(self._add_manual_email)
        search.addWidget(self.btn_add_manual, 0)
        box.addLayout(search)
        self.supplier_results = QListWidget(frame)
        self.supplier_results.setObjectName("recipientList")
        self.supplier_results.setAlternatingRowColors(False)
        self.supplier_results.setSpacing(scaled_px(3))
        self.supplier_results.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.supplier_results.setMinimumHeight(scaled_px(260))
        self.supplier_results.setItemDelegate(FreightRecipientDelegate(self.supplier_results))
        self.supplier_results.itemActivated.connect(self._toggle_recipient_item)
        self.supplier_results.itemClicked.connect(self._on_supplier_result_item_clicked)
        box.addWidget(self.supplier_results, 1)
        self.selected_label = QLabel("Nenhum destinatário selecionado")
        self.selected_label.setObjectName("muted")
        box.addWidget(self.selected_label, 0)
        return frame

    def _build_send_bar(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("sendBar")
        row = QHBoxLayout(frame)
        row.setContentsMargins(scaled_px(16), scaled_px(12), scaled_px(16), scaled_px(12))
        row.setSpacing(scaled_px(14))
        self.send_summary = QLabel("Pronto para começar")
        self.send_summary.setObjectName("sendSummary")
        self.send_summary.setWordWrap(True)
        row.addWidget(self.send_summary, 1)
        self.btn_review = QPushButton("Conferir")
        self.btn_review.setObjectName("secondaryAction")
        self.btn_review.clicked.connect(self._show_review_drawer)
        self.btn_review.setMinimumWidth(scaled_px(170))
        row.addWidget(self.btn_review, 0)
        self.btn_add_default_carriers = QPushButton(f"Adicionar padrão ({len(DEFAULT_FREIGHT_CARRIERS)})")
        self.btn_add_default_carriers.setObjectName("secondaryAction")
        self.btn_add_default_carriers.setToolTip("Adicionar as transportadoras padrão de frete")
        self.btn_add_default_carriers.clicked.connect(self._add_default_freight_carriers)
        self.btn_add_default_carriers.setMinimumWidth(scaled_px(190))
        self.btn_add_default_carriers.setVisible(False)
        row.addWidget(self.btn_add_default_carriers, 0)
        self.btn_clear = QPushButton("Limpar")
        self.btn_clear.setObjectName("secondarySmall")
        self.btn_clear.clicked.connect(self._clear_composer)
        self.btn_clear.setVisible(False)
        row.addWidget(self.btn_clear, 0)
        self.btn_send = QPushButton("Enviar")
        self.btn_send.setObjectName("accent")
        self.btn_send.setMinimumWidth(scaled_px(230))
        self.btn_send.clicked.connect(self._send_current_request)
        row.addWidget(self.btn_send, 0)
        return frame

    def _add_ex_spec_field(self, *, refresh: bool = False) -> None:
        field = QLineEdit(self)
        field.textChanged.connect(lambda *_: self._refresh_all())
        self.ex_spec_fields.append(field)
        label = QLabel("")
        wrapper = QFrame(self)
        wrapper.setObjectName("specInputRow")
        row_layout = QHBoxLayout(wrapper)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(scaled_px(6))
        row_layout.addWidget(field, 1)
        btn_remove = QPushButton("×")
        btn_remove.setObjectName("iconRemove")
        btn_remove.setToolTip("Remover especificação")
        btn_remove.setFixedWidth(scaled_px(30))
        btn_remove.clicked.connect(lambda _=False, w=wrapper: self._remove_ex_spec_field(w))
        row_layout.addWidget(btn_remove, 0)
        self.ex_spec_rows.append({"label": label, "field": field, "wrapper": wrapper, "button": btn_remove})
        self._rebuild_ex_specs_grid()
        if refresh:
            field.setFocus()
            self._refresh_all()

    def _rebuild_ex_specs_grid(self) -> None:
        while self.ex_specs_grid.count():
            item = self.ex_specs_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self.ex_specs_grid.removeWidget(widget)
        can_remove = len(self.ex_spec_rows) > 6
        for idx, row_data in enumerate(self.ex_spec_rows):
            label = row_data["label"]
            wrapper = row_data["wrapper"]
            button = row_data["button"]
            if isinstance(label, QLabel):
                label.setText(f"Especificação {idx + 1}")
            if isinstance(button, QPushButton):
                button.setVisible(can_remove and idx >= 6)
            row = idx // 2
            col = (idx % 2) * 2
            self.ex_specs_grid.addWidget(label, row, col)
            self.ex_specs_grid.addWidget(wrapper, row, col + 1)
        self.ex_specs_grid.setColumnStretch(1, 1)
        self.ex_specs_grid.setColumnStretch(3, 1)

    def _remove_ex_spec_field(self, wrapper: QWidget) -> None:
        if len(self.ex_spec_rows) <= 6:
            return
        row_data = next((row for row in self.ex_spec_rows if row.get("wrapper") is wrapper), None)
        if row_data is None:
            return
        field = row_data.get("field")
        self.ex_spec_rows.remove(row_data)
        self.ex_spec_fields = [row["field"] for row in self.ex_spec_rows if isinstance(row.get("field"), QLineEdit)]  # type: ignore[list-item]
        for widget in row_data.values():
            if isinstance(widget, QWidget):
                self.ex_specs_grid.removeWidget(widget)
                widget.hide()
                widget.deleteLater()
        self._rebuild_ex_specs_grid()
        if isinstance(field, QLineEdit) and clean_text(field.text()):
            self._set_status("Especificação removida.")
        self._refresh_all()

    def _current_ex_spec_values(self) -> list[str]:
        return [clean_text(field.text()) for field in self.ex_spec_fields if clean_text(field.text())]

    def _save_ex_panel(self) -> None:
        name = clean_text(self.ex_panel_name.text())
        specs = self._current_ex_spec_values()
        if not name:
            QMessageBox.information(self, "Painéis EX", "Informe o nome do painel antes de concluir.")
            self.ex_panel_name.setFocus()
            return
        if not specs:
            QMessageBox.information(self, "Painéis EX", "Informe pelo menos uma especificação do painel.")
            if self.ex_spec_fields:
                self.ex_spec_fields[0].setFocus()
            return
        panel = {"name": name, "specs": specs}
        if self._editing_ex_template_index is not None:
            index = self._editing_ex_template_index
            if 0 <= index < len(self._ex_panel_library):
                self._ex_panel_library[index] = panel
                save_ex_panel_library(self._ex_panel_library)
                self._set_status("Modelo de painel atualizado.")
            else:
                add_ex_panel_to_library(self._ex_panel_library, panel)
                self._set_status("Modelo de painel cadastrado.")
        else:
            if self._editing_ex_panel_index is None:
                self._ex_panels.append(panel)
                self._set_status("Painel cadastrado.")
            else:
                index = self._editing_ex_panel_index
                if 0 <= index < len(self._ex_panels):
                    self._ex_panels[index] = panel
                    self._set_status("Painel atualizado.")
                else:
                    self._ex_panels.append(panel)
                    self._set_status("Painel cadastrado.")
            add_ex_panel_to_library(self._ex_panel_library, panel)
        self._refresh_ex_templates_list()
        self._clear_ex_panel_form()
        self._refresh_ex_panels_list()
        self._refresh_all()

    def _refresh_ex_templates_list(self) -> None:
        if not hasattr(self, "ex_templates_list"):
            return
        self.ex_templates_list.clear()
        for index, panel in enumerate(self._ex_panel_library):
            specs = [clean_text(value) for value in panel.get("specs", []) if clean_text(value)]
            item = QListWidgetItem()
            item.setSizeHint(QSize(10, scaled_px(66 + min(3, len(specs)) * 16)))
            widget = self._ex_template_row_widget(index, clean_text(panel.get("name", "")), specs)
            self.ex_templates_list.addItem(item)
            self.ex_templates_list.setItemWidget(item, widget)

    def _ex_template_row_widget(self, index: int, name: str, specs: list[str]) -> QWidget:
        wrapper = QFrame(self.ex_templates_list)
        wrapper.setObjectName("exTemplateRow")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(scaled_px(10), scaled_px(7), scaled_px(10), scaled_px(7))
        layout.setSpacing(scaled_px(10))
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        title = QLabel(name)
        title.setObjectName("exPanelName")
        title.setWordWrap(True)
        text_box.addWidget(title)
        preview_specs = specs[:3]
        if len(specs) > 3:
            preview_specs.append(f"+ {len(specs) - 3} especificação(ões)")
        spec_label = QLabel("\n".join(f"- {spec}" for spec in preview_specs))
        spec_label.setObjectName("exPanelSpecs")
        spec_label.setWordWrap(True)
        text_box.addWidget(spec_label)
        layout.addLayout(text_box, 1)
        btn_use = QPushButton("Usar")
        btn_use.setObjectName("secondarySmall")
        btn_use.clicked.connect(lambda _=False, i=index: self._use_ex_panel_template(i))
        layout.addWidget(btn_use, 0)
        btn_edit = QPushButton("Editar")
        btn_edit.setObjectName("secondarySmall")
        btn_edit.clicked.connect(lambda _=False, i=index: self._edit_ex_panel_template(i))
        layout.addWidget(btn_edit, 0)
        btn_remove = QPushButton("×")
        btn_remove.setObjectName("iconRemove")
        btn_remove.setToolTip("Excluir modelo")
        btn_remove.setFixedWidth(scaled_px(32))
        btn_remove.clicked.connect(lambda _=False, i=index: self._remove_ex_panel_template(i))
        layout.addWidget(btn_remove, 0)
        return wrapper

    def _use_ex_panel_template(self, index: int) -> None:
        if index < 0 or index >= len(self._ex_panel_library):
            return
        panel = self._ex_panel_library[index]
        copied = {
            "name": clean_text(panel.get("name", "")),
            "specs": [clean_text(item) for item in panel.get("specs", []) if clean_text(item)],
        }
        if not copied["name"] or not copied["specs"]:
            return
        self._ex_panels.append(copied)
        self._refresh_ex_panels_list()
        self._refresh_all()
        self._set_status("Modelo adicionado aos painéis do e-mail.")

    def _show_ex_panel_form(self, *, editing_template: bool = False) -> None:
        self.ex_templates_card.hide()
        self.ex_form_card.show()
        self.btn_save_ex_panel.setText("Salvar modelo" if editing_template else "Concluir painel")
        self.btn_cancel_ex_edit.setVisible(True)
        self.ex_panel_name.setFocus()

    def _show_ex_templates(self) -> None:
        self.ex_form_card.hide()
        self.ex_templates_card.show()
        self._refresh_ex_templates_list()

    def _start_new_ex_panel(self) -> None:
        self._clear_ex_panel_form(show_templates=False)
        self._show_ex_panel_form(editing_template=False)
        self._set_status("Preencha o novo painel.")

    def _edit_ex_panel_template(self, index: int) -> None:
        if index < 0 or index >= len(self._ex_panel_library):
            return
        panel = self._ex_panel_library[index]
        specs = [clean_text(item) for item in panel.get("specs", []) if clean_text(item)]
        while len(self.ex_spec_fields) < max(6, len(specs)):
            self._add_ex_spec_field()
        self._editing_ex_template_index = index
        self._editing_ex_panel_index = None
        self.ex_panel_name.setText(clean_text(panel.get("name", "")))
        for idx, field in enumerate(self.ex_spec_fields):
            field.setText(specs[idx] if idx < len(specs) else "")
        self._show_ex_panel_form(editing_template=True)
        self._set_status("Modelo carregado para edição.")

    def _remove_ex_panel_template(self, index: int) -> None:
        if index < 0 or index >= len(self._ex_panel_library):
            return
        answer = QMessageBox.question(
            self,
            "Excluir modelo",
            "Excluir este modelo de painel?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._ex_panel_library.pop(index)
        save_ex_panel_library(self._ex_panel_library)
        self._refresh_ex_templates_list()
        self._set_status("Modelo excluído.")

    def _clear_ex_panel_form(self, *, show_templates: bool = True) -> None:
        self._editing_ex_panel_index = None
        self._editing_ex_template_index = None
        self.ex_panel_name.clear()
        for field in self.ex_spec_fields:
            field.clear()
        self.btn_save_ex_panel.setText("Concluir painel")
        self.btn_cancel_ex_edit.setVisible(True)
        if show_templates and hasattr(self, "ex_templates_card"):
            self._show_ex_templates()

    def _cancel_ex_panel_edit(self) -> None:
        self._clear_ex_panel_form(show_templates=True)
        self._set_status("Edição cancelada.")

    def _edit_ex_panel(self, index: int) -> None:
        if index < 0 or index >= len(self._ex_panels):
            return
        panel = self._ex_panels[index]
        specs = [clean_text(item) for item in panel.get("specs", []) if clean_text(item)]
        while len(self.ex_spec_fields) < max(6, len(specs)):
            self._add_ex_spec_field()
        self._editing_ex_panel_index = index
        self.ex_panel_name.setText(clean_text(panel.get("name", "")))
        for idx, field in enumerate(self.ex_spec_fields):
            field.setText(specs[idx] if idx < len(specs) else "")
        self.btn_save_ex_panel.setText("Salvar alterações")
        self.btn_cancel_ex_edit.setVisible(True)
        self._show_ex_panel_form(editing_template=False)
        self.ex_panel_name.setFocus()
        self._set_status("Painel carregado para edição.")

    def _remove_ex_panel(self, index: int) -> None:
        if index < 0 or index >= len(self._ex_panels):
            return
        self._ex_panels.pop(index)
        if self._editing_ex_panel_index == index:
            self._clear_ex_panel_form()
        elif self._editing_ex_panel_index is not None and self._editing_ex_panel_index > index:
            self._editing_ex_panel_index -= 1
        self._set_status("Painel removido.")
        self._refresh_ex_panels_list()
        self._refresh_all()

    def _refresh_ex_panels_list(self) -> None:
        self.ex_panels_list.clear()
        self.ex_panels_empty.setVisible(not self._ex_panels)
        for index, panel in enumerate(self._ex_panels):
            item = QListWidgetItem()
            specs = [clean_text(value) for value in panel.get("specs", []) if clean_text(value)]
            item.setSizeHint(QSize(10, scaled_px(70 + min(5, len(specs)) * 18)))
            widget = self._ex_panel_card_widget(index, clean_text(panel.get("name", "")), specs)
            self.ex_panels_list.addItem(item)
            self.ex_panels_list.setItemWidget(item, widget)

    def _ex_panel_card_widget(self, index: int, name: str, specs: list[str]) -> QWidget:
        wrapper = QFrame(self.ex_panels_list)
        wrapper.setObjectName("exPanelCard")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(scaled_px(10), scaled_px(8), scaled_px(10), scaled_px(8))
        layout.setSpacing(scaled_px(10))
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        title = QLabel(name)
        title.setObjectName("exPanelName")
        title.setWordWrap(True)
        text_box.addWidget(title)
        spec_text = "\n".join(f"- {spec}" for spec in specs)
        spec_label = QLabel(spec_text)
        spec_label.setObjectName("exPanelSpecs")
        spec_label.setWordWrap(True)
        text_box.addWidget(spec_label)
        layout.addLayout(text_box, 1)
        btn_edit = QPushButton("Editar")
        btn_edit.setObjectName("secondarySmall")
        btn_edit.clicked.connect(lambda _=False, i=index: self._edit_ex_panel(i))
        layout.addWidget(btn_edit, 0)
        btn_remove = QPushButton("×")
        btn_remove.setObjectName("iconRemove")
        btn_remove.setToolTip("Remover painel")
        btn_remove.setFixedWidth(scaled_px(32))
        btn_remove.clicked.connect(lambda _=False, i=index: self._remove_ex_panel(i))
        layout.addWidget(btn_remove, 0)
        return wrapper

    def _format_ex_panels_text(self) -> str:
        lines: list[str] = []
        for index, panel in enumerate(self._ex_panels, start=1):
            name = clean_text(panel.get("name", ""))
            if not name:
                continue
            lines.append(f"{index}. {name}")
            for spec in panel.get("specs", []):
                spec_text = clean_text(spec)
                if spec_text:
                    lines.append(f"   - {spec_text}")
            lines.append("")
        return "\n".join(lines).strip()

    def _install_paste_shortcut(self) -> None:
        shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._smart_paste_shortcut)
        quick = QShortcut(QKeySequence("Ctrl+K"), self)
        quick.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        quick.activated.connect(self._open_quick_actions)
        send = QShortcut(QKeySequence("Ctrl+Return"), self)
        send.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        send.activated.connect(self._send_current_request)

    def _setup_completer(self) -> None:
        # Não usar QCompleter cru aqui: ele separava empresa/e-mail em itens diferentes
        # e confundia o usuário. A lista de destinatários abaixo é o autocomplete real,
        # com cada fornecedor renderizado como um único item: Empresa • Contato • E-mail • Produto.
        try:
            self.quick_search.setCompleter(None)
        except Exception:
            pass

    # ---------- Navigation/helpers ----------
    def _review_checks(self) -> tuple[list[str], list[str]]:
        ok: list[str] = []
        warnings = self._validation_warnings(soft=True)
        recipients = self._current_recipients()
        emails = dedupe_emails(row.get("email", "") for row in recipients.values())
        if emails:
            ok.append(f"{len(emails)} destinatário(s) válido(s)")
        if self._request_type == REQUEST_FREIGHT:
            ok.append("Envio separado para cada transportadora")
        else:
            ok.append("Envio separado para cada fornecedor")
        ok.append(f"Empresa: {company_for_key(self._company_key).display_name}")
        owner = clean_text(self.signature_combo.currentText()) or "assinatura selecionada"
        ok.append(f"Assinatura: {owner}")
        ok.append(f"Anexos: {len(self._attachments)}")
        if self._request_type == REQUEST_MATERIAL and self.ex_required_check.isChecked():
            ok.append("Produto Ex: certificado/documentação solicitado")
        return ok, warnings

    def _show_review_drawer(self) -> None:
        try:
            subject, base_body, _ = self._build_current_email()
            body_html = self._preview_html(base_body)
            recipients = self._current_recipients()
            recipient_names = [f"{row.get('empresa') or 'Fornecedor'} <{row.get('email')}>" for row in recipients.values()]
            ok_checks, warnings = self._review_checks()
        except Exception as e:
            QMessageBox.critical(self, "Conferir envio", f"Não foi possível preparar o e-mail para conferência:\n{e}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Conferir envio")
        dialog.resize(scaled_px(820), scaled_px(680))
        box = QVBoxLayout(dialog)
        title = QLabel("Conferir antes de enviar")
        title.setStyleSheet(font_css(19, 850))
        box.addWidget(title)

        checklist = QFrame(dialog)
        checklist.setObjectName("subtlePanel")
        check_box = QVBoxLayout(checklist)
        check_box.setContentsMargins(scaled_px(12), scaled_px(10), scaled_px(12), scaled_px(10))
        check_box.setSpacing(scaled_px(5))
        for line in ok_checks:
            item = QLabel("✓ " + line)
            item.setObjectName("checkOk")
            check_box.addWidget(item)
        if warnings:
            warn_title = QLabel("Atenção:")
            warn_title.setObjectName("statusWarn")
            check_box.addWidget(warn_title)
            for line in warnings:
                item = QLabel("⚠ " + line)
                item.setObjectName("checkWarn")
                check_box.addWidget(item)
        else:
            item = QLabel("✓ Nenhuma pendência encontrada")
            item.setObjectName("checkOk")
            check_box.addWidget(item)
        box.addWidget(checklist, 0)

        subject_label = QLabel(f"<b>Assunto:</b> {html_lib.escape(subject)}")
        subject_label.setWordWrap(True)
        box.addWidget(subject_label)
        dest_label = QLabel("<b>Destinatários:</b> " + html_lib.escape("; ".join(recipient_names) or "nenhum"))
        dest_label.setWordWrap(True)
        box.addWidget(dest_label)
        preview = QTextBrowser(dialog)
        preview.setObjectName("previewBrowser")
        preview.setHtml(body_html)
        box.addWidget(preview, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        btn_close = QPushButton("Voltar")
        btn_close.clicked.connect(dialog.reject)
        actions.addWidget(btn_close)
        btn_send = QPushButton(self._build_current_email()[2])
        btn_send.setObjectName("accent")
        btn_send.clicked.connect(dialog.accept)
        actions.addWidget(btn_send)
        box.addLayout(actions)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._send_current_request(confirm_review=False)

    def _open_history_area(self) -> None:
        if self._on_open_history:
            self._on_open_history()
            return
        self._show_quick_help()

    def _open_material_from_suppliers(self) -> None:
        if not getattr(self.app_context.state, "selected_emails", set()):
            QMessageBox.information(self, "Fornecedores", "Selecione pelo menos um fornecedor para montar a cotação.")
            return
        self._set_request_type(REQUEST_MATERIAL, manual=True)
        self.import_selected_emails_from_state()
        self.smart_input.setFocus()
        self._set_status("Fornecedores selecionados adicionados aos destinatários.")

    def _open_more_menu(self) -> None:
        menu = QMenu(self)
        act_suppliers = QAction("Base de fornecedores", self)
        act_suppliers.triggered.connect(lambda: self._on_open_suppliers() if self._on_open_suppliers else self._set_request_type(REQUEST_SUPPLIERS, manual=True))
        menu.addAction(act_suppliers)
        act_history = QAction("Histórico / recentes", self)
        act_history.triggered.connect(lambda: self._on_open_history() if self._on_open_history else None)
        menu.addAction(act_history)
        act_clear = QAction("Limpar tela atual", self)
        act_clear.triggered.connect(self._clear_composer)
        menu.addAction(act_clear)
        menu.addSeparator()
        act_theme = QAction("Alternar tema", self)
        act_theme.triggered.connect(lambda: self._on_cycle_theme() if self._on_cycle_theme else None)
        menu.addAction(act_theme)
        act_admin = QAction("Admin (Ctrl+Alt+A)", self)
        act_admin.triggered.connect(lambda: self._on_open_settings() if self._on_open_settings else None)
        menu.addAction(act_admin)
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    def _show_quick_help(self) -> None:
        QMessageBox.information(
            self,
            "Como usar rápido",
            "Comece em Fornecedores para buscar por produto, marque os destinatários e clique em Montar cotação.\n"
            "Depois escolha Material, Painéis EX, Frete ou Ordem de compra, confira e envie.\n\n"
            "Atalhos: Ctrl+V colar/anexar, Ctrl+K ações rápidas, Ctrl+Enter enviar, Ctrl+Alt+A admin.",
        )

    def _open_quick_actions(self) -> None:
        actions = ["Fornecedores", "Cotar material", "Painéis EX", "Cotar frete", "Enviar ordem de compra", "Colar conteúdo", "Conferir e-mail", "Ver recentes", "Abrir admin"]
        choice, ok = QInputDialog.getItem(self, "Ações rápidas", "O que deseja fazer?", actions, 0, False)
        if not ok:
            return
        if choice == "Fornecedores":
            if self._on_open_suppliers:
                self._on_open_suppliers()
            else:
                self._set_request_type(REQUEST_SUPPLIERS, manual=True)
        elif choice == "Cotar material":
            self._set_request_type(REQUEST_MATERIAL, manual=True)
            self.smart_input.setFocus()
        elif choice == "Painéis EX":
            self._set_request_type(REQUEST_EX_PANELS, manual=True)
            self.ex_panel_name.setFocus()
        elif choice == "Cotar frete":
            self._set_request_type(REQUEST_FREIGHT, manual=True)
            self.freight_desc.setFocus()
        elif choice == "Enviar ordem de compra":
            self._set_request_type(REQUEST_PO, manual=True)
            self.po_number.setFocus()
        elif choice == "Colar conteúdo":
            self._paste_from_clipboard()
        elif choice == "Conferir e-mail":
            self._show_review_drawer()
        elif choice == "Ver recentes" and self._on_open_history:
            self._on_open_history()
        elif choice == "Abrir admin" and self._on_open_settings:
            self._on_open_settings()

    # ---------- Identity and modes ----------
    def _apply_initial_identity(self) -> None:
        owner_options = signature_owner_options(self.app_context.state.config)
        if not owner_options:
            owner_options = [first_signature_owner(self.app_context.state.config) or ""]
        self.signature_combo.clear()
        self.signature_combo.addItems([item for item in owner_options if item])
        identity = current_signature_identity()
        resolved_owner, source = resolve_signature_owner(self.app_context.state.config)
        if resolved_owner:
            idx = self.signature_combo.findText(resolved_owner)
            if idx >= 0:
                self.signature_combo.setCurrentIndex(idx)
        self.identity_hint.setText(f"PC: {identity.machine}  |  Usuário: {identity.username}  |  {source}")

    def _custom_type_fields(self) -> list[dict[str, Any]]:
        if not self._custom_quote_type:
            return []
        fields = self._custom_quote_type.get("fields")
        return [f for f in (fields if isinstance(fields, list) else []) if isinstance(f, dict)][:8]

    def _clear_custom_fields_grid(self) -> None:
        while self.custom_fields_grid.count():
            item = self.custom_fields_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._custom_field_widgets.clear()

    def _render_custom_fields(self) -> None:
        if not hasattr(self, "custom_fields_card"):
            return
        self._clear_custom_fields_grid()
        if not self._custom_quote_type:
            self.custom_fields_card.hide()
            self.smart_input.show()
            self.ex_required_check.show()
            if self.material_title_label:
                self.material_title_label.setText("Material para cotar")
            if self.material_hint_label:
                self.material_hint_label.setText("Cole ou digite os itens exatamente como quer que o fornecedor receba.")
            self.smart_input.setPlaceholderText("Cole ou digite o material aqui...")
            return
        self.ex_required_check.hide()
        if self.material_title_label:
            self.material_title_label.setText(str(self._custom_quote_type.get("name") or "Envio personalizado"))
        if self.material_hint_label:
            self.material_hint_label.setText(str(self._custom_quote_type.get("description") or "Preencha os campos deste tipo de envio."))
        fields = self._custom_type_fields()
        if not fields:
            self.custom_fields_card.hide()
            self.smart_input.show()
            self.smart_input.setPlaceholderText("Digite o conteúdo deste envio...")
            return
        self.smart_input.hide()
        self.custom_fields_card.show()
        for idx, field in enumerate(fields):
            label_text = str(field.get("label") or field.get("var") or f"Campo {idx+1}")
            if bool(field.get("required", True)):
                label_text += " *"
            lab = QLabel(label_text)
            lab.setObjectName("formLabel")
            r = idx // 2
            c = (idx % 2) * 2
            self.custom_fields_grid.addWidget(lab, r * 2, c)
            if bool(field.get("multiline", False)):
                widget = QTextEdit(self.custom_fields_card)
                widget.setMinimumHeight(scaled_px(88))
                widget.textChanged.connect(self._refresh_all)
                widget.textChanged.connect(self._refresh_supplier_suggestions)
                widget.setPlaceholderText(str(field.get("placeholder") or ""))
            else:
                widget = QLineEdit(self.custom_fields_card)
                widget.textChanged.connect(lambda *_: (self._refresh_all(), self._refresh_supplier_suggestions()))
                widget.setPlaceholderText(str(field.get("placeholder") or ""))
            var = str(field.get("var") or label_text).upper()
            self._custom_field_widgets[var] = widget
            self.custom_fields_grid.addWidget(widget, r * 2 + 1, c, 1, 2 if bool(field.get("multiline", False)) else 1)
        self.custom_fields_grid.setColumnStretch(1, 1)
        self.custom_fields_grid.setColumnStretch(3, 1)

    def _custom_field_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for var, widget in self._custom_field_widgets.items():
            if isinstance(widget, QTextEdit):
                values[var] = clean_text(widget.toPlainText())
            elif isinstance(widget, QLineEdit):
                values[var] = clean_text(widget.text())
            else:
                values[var] = ""
        if "CONTEUDO" not in values:
            lines = []
            for field in self._custom_type_fields():
                var = str(field.get("var") or "").upper()
                label = str(field.get("label") or var)
                value = values.get(var, "")
                if value:
                    lines.append(f"{label}: {value}")
            values["CONTEUDO"] = "\n".join(lines).strip()
        return values

    def _apply_custom_template(self, template: str, *, company_prefix: str, title: str, include_signature_token: bool = False) -> str:
        text = str(template or "")
        values = self._custom_field_values()
        replacements = {
            "EMPRESA": company_prefix,
            "TITULO": title,
            "TIPO": str(self._custom_quote_type.get("name") if self._custom_quote_type else ""),
            "CONTEUDO": values.get("CONTEUDO", ""),
            "ASSINATURA": "{ASSINATURA}" if include_signature_token else "",
        }
        replacements.update(values)
        for key, value in replacements.items():
            text = text.replace("{" + key + "}", str(value or ""))
        return text.strip()

    def _set_request_type(self, request_type: str, *, manual: bool = True) -> None:
        self._custom_quote_type = None
        original_request_type = request_type
        if str(request_type).startswith("custom:"):
            custom_id = str(request_type).split(":", 1)[1]
            for item in list(getattr(self.app_context.state.config, "custom_quote_types", []) or []):
                if isinstance(item, dict) and str(item.get("id") or "") == custom_id and item.get("active", True):
                    self._custom_quote_type = item
                    request_type = REQUEST_MATERIAL
                    break
        if request_type not in {REQUEST_SUPPLIERS, REQUEST_MATERIAL, REQUEST_EX_PANELS, REQUEST_FREIGHT, REQUEST_PO}:
            request_type = REQUEST_SUPPLIERS
        previous_type = self._request_type
        self._request_type = request_type
        freeze_paint = request_type == REQUEST_FREIGHT
        if freeze_paint:
            self.setUpdatesEnabled(False)
            if hasattr(self, "supplier_results"):
                self.supplier_results.setUpdatesEnabled(False)
        try:
            if manual:
                self._request_type_was_manual = True
            if request_type == REQUEST_FREIGHT and previous_type != REQUEST_FREIGHT:
                self._carrier_hidden_session.clear()
            for key, button in self.type_buttons.items():
                button.setChecked(key == request_type)
            target_index = {
                REQUEST_SUPPLIERS: 0,
                REQUEST_MATERIAL: 1,
                REQUEST_EX_PANELS: 2,
                REQUEST_FREIGHT: 3,
                REQUEST_PO: 4,
            }.get(request_type, 0)
            if self.task_stack.currentIndex() != target_index:
                self.task_stack.setCurrentIndex(target_index)
            if request_type == REQUEST_SUPPLIERS:
                self.recipients_title.setText("Destinatários")
                self.recipients_hint.setText("Marque fornecedores e clique em Montar cotação.")
                self.quick_search.setPlaceholderText("Buscar fornecedor ou e-mail")
                self.btn_add_manual.setText("Adicionar")
            elif request_type == REQUEST_FREIGHT:
                self.recipients_title.setText("Transportadoras")
                self.recipients_hint.setText("Selecione as transportadoras que receberão a cotação de frete.")
                self.quick_search.setPlaceholderText("Buscar transportadora, fornecedor ou e-mail")
                self.btn_add_manual.setText("Adicionar")
            elif request_type == REQUEST_EX_PANELS:
                self.recipients_title.setText("Destinatários")
                self.recipients_hint.setText("Escolha os fornecedores que receberão a cotação de Painéis EX.")
                self.quick_search.setPlaceholderText("Buscar fornecedor ou e-mail")
                self.btn_add_manual.setText("Adicionar")
            else:
                self.recipients_title.setText("Destinatários")
                if self._custom_quote_type:
                    self.recipients_hint.setText(f"Escolha os fornecedores que receberão {self._custom_quote_type.get('name', 'este envio')}.")
                else:
                    self.recipients_hint.setText("Escolha os fornecedores que receberão este envio.")
                self.quick_search.setPlaceholderText("Buscar fornecedor, produto ou e-mail")
                self.btn_add_manual.setText("Adicionar")
            if request_type == REQUEST_MATERIAL:
                self._render_custom_fields()
            if request_type in {REQUEST_MATERIAL, REQUEST_EX_PANELS, REQUEST_PO}:
                self.import_selected_emails_from_state()
            self._apply_responsive_layout(force=True)
            self._refresh_freight_defaults_button()
            self._refresh_all()
            self._refresh_supplier_suggestions()
        finally:
            if freeze_paint:
                if hasattr(self, "supplier_results"):
                    self.supplier_results.setUpdatesEnabled(True)
                    self.supplier_results.viewport().update()
                self.setUpdatesEnabled(True)
                self.update()

    def _set_company(self, company_key: str) -> None:
        if company_key not in COMPANIES:
            company_key = "vesper"
        self._company_key = company_key
        cfg = self.app_context.state.config
        cfg.default_company_key = company_key
        cfg.smtp_active_profile = company_for_key(company_key).smtp_profile
        try:
            cfg.save()
        except Exception:
            pass
        self.btn_company_vesper.setChecked(company_key == "vesper")
        self.btn_company_ventrio.setChecked(company_key == "ventrio")
        if not getattr(self, "_is_initializing", False):
            self._refresh_all()

    def _save_signature_map_for_identity(self) -> None:
        owner = clean_text(self.signature_combo.currentText())
        if not owner:
            return
        identity = current_signature_identity()
        cfg = self.app_context.state.config
        try:
            if not isinstance(getattr(cfg, "signature_auto_map", None), dict):
                cfg.signature_auto_map = {}
            cfg.signature_auto_map[identity.compound_key] = owner
            if identity.username:
                cfg.signature_auto_map[identity.username.lower()] = owner
            cfg.last_signature_owner = owner
            cfg.save()
            self.identity_hint.setText(f"PC: {identity.machine}  |  Usuário: {identity.username}  |  assinatura salva para este PC")
            self._set_status("Assinatura padrão salva para este computador.")
        except Exception as exc:
            QMessageBox.warning(self, "Assinatura", f"Não foi possível salvar: {exc}")

    # ---------- Smart analysis ----------
    def _schedule_analysis(self) -> None:
        # O texto do material alimenta somente a prévia/análise do envio.
        # A busca de destinatários é controlada exclusivamente pelo campo
        # "Buscar fornecedor, produto ou e-mail". Isso evita a travada e o
        # comportamento confuso em que o painel de destinatários mudava
        # enquanto o usuário digitava o material a cotar.
        self._analysis_timer.start()

    def _schedule_suggestions(self) -> None:
        self._suggest_timer.start()

    def _analyze_and_refresh(self) -> None:
        had_material_focus = self.smart_input.hasFocus()
        cursor = self.smart_input.textCursor()
        text = self.smart_input.toPlainText()
        analysis = analyze_smart_input(text, self._attachments)
        self._last_analysis = analysis
        # Não trocar de modo enquanto o usuário está digitando no material.
        # Essa troca escondia o campo, roubava foco e fazia parecer que a tela
        # travou. Mudança automática fica restrita a momentos em que o campo
        # não está ativo e a confiança é muito alta.
        if (
            not had_material_focus
            and not self._request_type_was_manual
            and analysis.request_type != self._request_type
            and analysis.confidence >= 92
        ):
            self._set_request_type(analysis.request_type, manual=False)
        self._apply_analysis_to_fields(analysis)
        self._auto_add_detected_emails(analysis.emails)
        self._refresh_preview()
        self._update_labels()
        if had_material_focus:
            self.smart_input.setTextCursor(cursor)
            self.smart_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_analysis_to_fields(self, analysis: SmartAnalysis) -> None:
        self._updating_freight_fields = True
        try:
            fields = analysis.freight_fields or {}
            for key, widget in (
                ("descricao", self.freight_desc),
                ("volumes", self.freight_volumes),
                ("peso", self.freight_weight),
                ("valor_nf", self.freight_nf_value),
                ("medidas", self.freight_measures),
            ):
                value = clean_text(fields.get(key, ""))
                if value and not clean_text(widget.text()):
                    with QSignalBlocker(widget):
                        widget.setText(value)
        finally:
            self._updating_freight_fields = False
        if analysis.oc_number and not clean_text(self.po_number.text()):
            with QSignalBlocker(self.po_number):
                self.po_number.setText(analysis.oc_number)
        if analysis.ex_required and not self.ex_required_check.isChecked():
            with QSignalBlocker(self.ex_required_check):
                self.ex_required_check.setChecked(True)

    def _auto_add_detected_emails(self, emails: list[str]) -> None:
        for email in emails:
            key = normalize_text(email)
            if not key or key in self._auto_emails_added:
                continue
            self._auto_emails_added.add(key)
            if self._request_type == REQUEST_FREIGHT:
                self._extra_carriers[key] = {"empresa": self._company_from_email(email), "email": email, "contato_nome": "", "telefone": ""}
                self._carrier_selected.add(key)
            else:
                supplier = None
                try:
                    supplier = self.app_context.state.index.get_by_email(email)
                except Exception:
                    supplier = None
                row = self._supplier_to_recipient(supplier) if supplier is not None else {"empresa": self._company_from_email(email), "email": email, "contato_nome": "", "telefone": ""}
                self._selected_recipients[key] = row

    def _schedule_freight_refresh(self) -> None:
        if getattr(self, "_updating_freight_fields", False):
            return
        # Campos de frete não devem redesenhar a lista de transportadoras nem
        # bloquear a troca de tela. Atualiza apenas resumo/botão/anexos com
        # debounce curto.
        self._freight_refresh_timer.start()

    def _refresh_all(self) -> None:
        self._refresh_preview()
        self._update_labels()
        self._refresh_drop_zones()

    def _request_label(self) -> str:
        if self._custom_quote_type:
            return str(self._custom_quote_type.get("name") or "Envio personalizado")
        return {
            REQUEST_SUPPLIERS: "Fornecedores",
            REQUEST_MATERIAL: "Material",
            REQUEST_EX_PANELS: "Painéis EX",
            REQUEST_FREIGHT: "Frete",
            REQUEST_PO: "Ordem de compra",
        }.get(self._request_type, "Solicitação")

    def _build_current_email(self) -> tuple[str, str, str]:
        company = company_for_key(self._company_key)
        smart_text = self.smart_input.toPlainText().strip()
        if self._request_type == REQUEST_EX_PANELS:
            return self._build_ex_panels_email(company)
        if self._request_type == REQUEST_FREIGHT:
            subject, body = build_freight_email(
                company,
                descricao=self.freight_desc.text().strip(),
                volumes=self.freight_volumes.text().strip(),
                peso=self.freight_weight.text().strip(),
                valor_nf=self.freight_nf_value.text().strip(),
                medidas=self.freight_measures.text().strip(),
                observacao=self.freight_destination.text().strip(),
            )
            return subject, body, "Enviar frete"
        if self._request_type == REQUEST_PO:
            subject, body = build_purchase_order_email(
                company,
                oc_number=self.po_number.text().strip(),
                observacao=self.po_supplier_hint.text().strip(),
            )
            return subject, body, "Enviar OC"
        material_text = strip_email_only_lines(smart_text)
        if self._custom_quote_type:
            company_prefix = getattr(company, "subject_prefix", "VESPER")
            title = summarize_subject(material_text or str(self._custom_quote_type.get("name") or "Envio"))
            subject_template = str(self._custom_quote_type.get("subject_template") or "{EMPRESA} <> COTAÇÃO <> {TITULO}")
            body_template = str(self._custom_quote_type.get("body_template") or "Prezados,\n\nSolicito cotação para:\n\n{CONTEUDO}\n\nFico no aguardo.\n\n{ASSINATURA}")
            subject = subject_template.replace("{EMPRESA}", company_prefix).replace("{TITULO}", title).replace("{TIPO}", str(self._custom_quote_type.get("name") or ""))
            body = body_template.replace("{CONTEUDO}", material_text or "MATERIAL").replace("{TIPO}", str(self._custom_quote_type.get("name") or "")).replace("{ASSINATURA}", "").strip()
            return subject, body, "Enviar cotação"
        subject, body = build_material_email(company, material_text or "MATERIAL", ex_required=self.ex_required_check.isChecked())
        return subject, body, "Enviar cotação"

    def _build_ex_panels_email(self, company: object) -> tuple[str, str, str]:
        panels_text = self._format_ex_panels_text()
        subject = f"{getattr(company, 'subject_prefix', 'VESPER')} <> COTAÇÃO <> Painéis Elétricos Ex"
        body = f"""Prezados,

Solicito, por gentileza, o envio de cotação para os painéis elétricos abaixo, conforme as especificações:

{panels_text or "Nenhum painel cadastrado."}

Solicito, por gentileza, que a proposta comercial contenha as seguintes informações:

- Valor unitário e valor total;
- Prazo de entrega;
- Condições de pagamento;
- Informações sobre o frete;
- Certificado ou documentação que comprove que o produto é adequado para utilização em área classificada ou para instalação em invólucro Ex.

Endereço para entrega/coleta:

Av. Exemplo, 100
Centro – Cidade/UF
CEP: 00000-000

Horário de funcionamento:

Atendimento em horário comercial.

Desde já, agradeço a atenção e fico no aguardo do seu retorno.

Obrigada!"""
        return subject, body, "Enviar cotação"

    def _refresh_preview(self) -> None:
        self.btn_send.setText(self._build_current_email()[2])

    def _refresh_drop_zones(self) -> None:
        state = tuple(self._attachments)
        if state == self._drop_zone_files_state:
            return
        self._drop_zone_files_state = state
        for zone in (self.material_drop_zone, self.ex_drop_zone, self.freight_drop_zone, self.po_drop_zone):
            zone.set_files(self._attachments)

    def _update_labels(self) -> None:
        company = company_for_key(self._company_key)
        recipients = self._current_recipients()
        recipient_count = len(dedupe_emails(row.get("email", "") for row in recipients.values()))
        attach_count = len(self._attachments)
        recipient_word = "transportadora(s)" if self._request_type == REQUEST_FREIGHT else "destinatário(s)"
        empty_text = "Nenhuma transportadora selecionada" if self._request_type == REQUEST_FREIGHT else "Nenhum destinatário selecionado"
        selected = f"{recipient_count} {recipient_word} selecionado(s)" if recipient_count else empty_text
        self.selected_label.setText(selected)
        signature = clean_text(self.signature_combo.currentText()) or "assinatura nao definida"
        self.send_summary.setText(f"{selected}  |  {attach_count} anexo(s)  |  {company.label}  |  {signature}")
        self._refresh_freight_defaults_button()

    # ---------- Recipient list ----------
    def import_selected_emails_from_state(self) -> None:
        selected = list(getattr(self.app_context.state, "selected_emails", set()) or [])
        for email in selected:
            if not is_valid_email(str(email)):
                continue
            supplier = None
            try:
                supplier = self.app_context.state.index.get_by_email(str(email))
            except Exception:
                supplier = None
            row = self._supplier_to_recipient(supplier) if supplier is not None else {"empresa": self._company_from_email(str(email)), "email": str(email), "contato_nome": "", "telefone": ""}
            self._selected_recipients[normalize_text(str(email))] = row
        self._refresh_all()

    def _suggestion_query(self) -> str:
        return clean_text(self.quick_search.text())

    def _index_supplier_count(self) -> int:
        index = getattr(self.app_context.state, "index", None)
        if index is None:
            return 0
        try:
            if hasattr(index, "supplier_count"):
                return int(getattr(index, "supplier_count") or 0)
            if hasattr(index, "suppliers"):
                return len(list(getattr(index, "suppliers") or []))
            if hasattr(index, "get_all_suppliers"):
                return len(list(index.get_all_suppliers() or []))
        except Exception:
            return 0
        return 0

    def _ensure_recipient_base_loaded_async(self) -> None:
        if self._recipient_base_loading:
            return
        if self._index_supplier_count() > 0:
            self._recipient_base_loaded_once = True
            return
        self._recipient_base_loading = True
        signals = _RecipientBaseSignals(self)
        self._recipient_base_signals = signals
        signals.done.connect(self._on_recipient_base_loaded)
        signals.error.connect(self._on_recipient_base_error)
        self._thread_pool.start(_RecipientBaseLoadRunnable(app_context=self.app_context, signals=signals))

    def _on_recipient_base_loaded(self, _result: object) -> None:
        self._recipient_base_loading = False
        self._recipient_base_loaded_once = True
        self._invalidate_recipient_cache()
        self._refresh_supplier_suggestions()
        self._set_status("Base de fornecedores pronta para busca.")

    def _on_recipient_base_error(self, message: str) -> None:
        self._recipient_base_loading = False
        self._recipient_base_loaded_once = True
        self._set_status("Não foi possível carregar a base de fornecedores para destinatários.")
        if self._suggestion_query():
            self._populate_system_message("Base de fornecedores indisponível. Abra Fornecedores ou verifique Configurações.")

    def _refresh_supplier_suggestions(self) -> None:
        if self._request_type == REQUEST_SUPPLIERS:
            return
        query = self._suggestion_query()
        # A lista de destinatários nunca deve mudar por causa do texto digitado
        # no material; só muda por busca explícita no campo de destinatários.
        if self._request_type == REQUEST_FREIGHT:
            # Frete precisa mostrar visualmente as transportadoras selecionadas
            # para usuário leigo, mas sem criar QWidget pesado por card. A lista
            # de frete usa itens nativos/delegate do próprio QListWidget: assim
            # aparecem os 7 selecionados e evitamos a janela órfã causada por
            # setItemWidget em lote no Windows.
            rows = self._carrier_rows(query)
            if not rows:
                if query:
                    self._populate_system_message(
                        "Nenhum fornecedor ou transportadora encontrado para essa busca. Confira produto, nome, e-mail ou adicione manualmente."
                    )
                else:
                    self._populate_freight_selection_summary()
                return
        else:
            if query and self._index_supplier_count() <= 0:
                self._ensure_recipient_base_loaded_async()
                self._populate_system_message("Carregando fornecedores... a busca aparecerá automaticamente.")
                return
            rows = self._recipient_rows(query)
            if query and not rows and self._recipient_base_loading:
                self._populate_system_message("Carregando fornecedores... a busca aparecerá automaticamente.")
                return
        self._populate_recipient_list(rows)

    def _carrier_rows(self, query: str) -> list[dict[str, str]]:
        q = normalize_text(query)
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        def add_row(row: dict[str, str]) -> None:
            email = clean_text(row.get("email", ""))
            key = normalize_text(email)
            if not key or key in seen or not is_valid_email(email):
                return
            seen.add(key)
            rows.append(row)

        for carrier in DEFAULT_FREIGHT_CARRIERS:
            key = normalize_text(carrier.email)
            if key in self._carrier_hidden_session and key not in self._carrier_selected:
                continue
            haystack = normalize_text(f"{carrier.label} {carrier.email}")
            if q:
                if q not in haystack:
                    continue
            elif key not in self._carrier_selected:
                # Frete abre leve, igual aos outros fluxos: sem renderizar as 7
                # transportadoras padrão até o usuário pedir explicitamente.
                continue
            add_row({"empresa": carrier.label, "email": carrier.email, "kind": "carrier", "kind_label": "Transportadora padrão"})

        for row in self._extra_carriers.values():
            key = normalize_text(row.get("email", ""))
            haystack = normalize_text(f"{row.get('empresa','')} {row.get('email','')}")
            if q:
                if q not in haystack:
                    continue
            elif key not in self._carrier_selected:
                continue
            data = dict(row)
            data["kind"] = "extra_carrier"
            data["kind_label"] = data.get("kind_label") or "Transportadora adicionada"
            add_row(data)

        if q:
            # Frete agora usa a mesma base de busca dos destinatários comuns.
            # As transportadoras padrão continuam no topo quando batem, mas a
            # busca por produto/fornecedor/e-mail encontra a base global também.
            if self._index_supplier_count() <= 0 and not self._recipient_base_loading:
                self._ensure_recipient_base_loaded_async()
            for row in self._search_recipient_rows(query):
                data = dict(row)
                data["kind"] = data.get("kind") or "supplier"
                data["kind_label"] = data.get("produto") or "Fornecedor da base"
                add_row(data)

        return rows

    def _is_reliable_query(self, query: str) -> bool:
        q = clean_text(query).strip()
        if len(q) < 3:
            return False
        if q.lower() in {"asd", "qwe", "zxc", "abc", "123", "aaaa", "test", "teste"}:
            return False
        try:
            index = self.app_context.state.index
            if index:
                q_norm = normalize_text(q)
                for s in index.suppliers:
                    if q_norm in normalize_text(s.name) or q_norm in normalize_text(s.product_tags):
                        return True
        except Exception:
            pass
        return False

    def _recipient_rows(self, query: str) -> list[dict[str, str]]:
        selected = list(self._selected_recipients.values())
        effective_query = clean_text(query)
        candidates = selected
        if effective_query:
            candidates += self._search_recipient_rows(effective_query)
        return search_recipient_rows(
            candidates,
            effective_query,
            selected_emails=(self._carrier_selected if self._request_type == REQUEST_FREIGHT else self._selected_recipients.keys()),
            limit=12,
        )

    def _default_search_text(self) -> str:
        # Mantido apenas por compatibilidade interna. A busca de destinatários
        # deixou de usar o material como consulta automática para preservar foco,
        # previsibilidade e performance durante a digitação.
        return ""

    def _default_freight_carrier_keys(self) -> set[str]:
        return {normalize_text(carrier.email) for carrier in DEFAULT_FREIGHT_CARRIERS if is_valid_email(carrier.email)}

    def _add_default_freight_carriers(self) -> None:
        if self._request_type != REQUEST_FREIGHT:
            return
        self._carrier_hidden_session.clear()
        self._carrier_selected.update(self._default_freight_carrier_keys())
        with QSignalBlocker(self.quick_search):
            self.quick_search.clear()
        self._recipient_visible_signature = tuple()
        # Renderiza os 7 selecionados por delegate: visual de card igual aos
        # destinatários comuns, sem criar QWidget por linha em lote.
        self._refresh_supplier_suggestions()
        self._update_labels()
        self._set_status(f"{len(DEFAULT_FREIGHT_CARRIERS)} transportadoras padrão adicionadas ao frete.")

    def _refresh_freight_defaults_button(self) -> None:
        btn = getattr(self, "btn_add_default_carriers", None)
        if btn is None:
            return
        is_freight = self._request_type == REQUEST_FREIGHT
        btn.setVisible(is_freight)
        if not is_freight:
            return
        default_keys = self._default_freight_carrier_keys()
        selected_defaults = len(default_keys.intersection(self._carrier_selected))
        total = len(default_keys) or len(DEFAULT_FREIGHT_CARRIERS)
        missing = selected_defaults < total
        btn.setEnabled(missing)
        btn.setObjectName("secondaryAction" if missing else "secondarySmall")
        btn.setMinimumWidth(scaled_px(190 if missing else 122))
        btn.setText(f"Adicionar padrão ({total})" if missing else "Padrão ✓")
        btn.setToolTip("Adicionar as transportadoras padrão de frete" if missing else "Padrão de frete já adicionado")
        btn.style().unpolish(btn); btn.style().polish(btn)


    def _populate_freight_selection_summary(self) -> None:
        selected_count = len(self._carrier_selected)
        default_total = len(self._default_freight_carrier_keys()) or len(DEFAULT_FREIGHT_CARRIERS)
        if selected_count <= 0:
            title = "Nenhuma transportadora selecionada"
            message = "Use a busca acima, adicione um e-mail manualmente ou clique em Adicionar padrão (7) na barra inferior."
            signature = (("__freight_empty__", False, 0),)
        else:
            title = f"{selected_count} transportadora(s) selecionada(s)"
            if selected_count >= default_total:
                message = "Padrão de frete pronto para envio. Para revisar uma transportadora específica, use a busca acima."
            else:
                message = "Seleção de frete pronta para envio. Use a busca acima para revisar, adicionar ou remover transportadoras."
            signature = (("__freight_summary__", True, selected_count),)
        if signature == self._recipient_visible_signature:
            return
        self._recipient_visible_signature = signature
        self.supplier_results.setUpdatesEnabled(False)
        try:
            self.supplier_results.clear()
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setText(f"{title}\n{message}")
            item.setSizeHint(QSize(10, scaled_px(82)))
            self.supplier_results.addItem(item)
        finally:
            self.supplier_results.setUpdatesEnabled(True)
            self.supplier_results.viewport().update()

    def _populate_system_message(self, message: str) -> None:
        signature = (("__system__", bool(message)),)
        if signature == self._recipient_visible_signature:
            return
        self._recipient_visible_signature = signature
        self.supplier_results.setUpdatesEnabled(False)
        try:
            self.supplier_results.clear()
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(10, scaled_px(92)))
            frame = QFrame(self.supplier_results.viewport())
            frame.setObjectName("emptyStateCard")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(scaled_px(14), scaled_px(12), scaled_px(14), scaled_px(12))
            title = QLabel("Nada selecionado ainda")
            title.setObjectName("recipientName")
            title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            text = QLabel(clean_text(message))
            text.setObjectName("recipientEmail")
            text.setWordWrap(True)
            text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(title)
            layout.addWidget(text)
            self.supplier_results.addItem(item)
            self.supplier_results.setItemWidget(item, frame)
        finally:
            self.supplier_results.setUpdatesEnabled(True)
            self.supplier_results.viewport().update()

    def _populate_recipient_list(self, rows: list[dict[str, str]]) -> None:
        selected_keys = self._carrier_selected if self._request_type == REQUEST_FREIGHT else self._selected_recipients.keys()
        signature = tuple((normalize_text(clean_text(row.get("email", ""))), normalize_text(clean_text(row.get("email", ""))) in selected_keys) for row in rows)
        if signature == self._recipient_visible_signature:
            return
        self._recipient_visible_signature = signature
        self.supplier_results.setUpdatesEnabled(False)
        try:
            self.supplier_results.clear()
            for row in rows:
                email = clean_text(row.get("email"))
                if not is_valid_email(email):
                    continue
                key = normalize_text(email)
                company = clean_text(row.get("empresa")) or self._company_from_email(email)
                checked = key in (self._carrier_selected if self._request_type == REQUEST_FREIGHT else self._selected_recipients)
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, dict(row))
                if self._request_type == REQUEST_FREIGHT:
                    # Card visual por delegate: mantém o mesmo padrão visual dos
                    # destinatários comuns, mas sem QWidget/setItemWidget em lote.
                    item.setData(FreightRecipientDelegate.ROLE_FREIGHT_CARD, True)
                    item.setData(Qt.ItemDataRole.CheckStateRole, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                    item.setToolTip(f"{company}\n{email}\nClique para {'remover' if checked else 'adicionar'}")
                    item.setSizeHint(QSize(10, scaled_px(70)))
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.supplier_results.addItem(item)
                else:
                    item.setSizeHint(QSize(10, scaled_px(70)))
                    widget = self._recipient_row_widget(company=company, email=email, checked=checked, row=row)
                    self.supplier_results.addItem(item)
                    self.supplier_results.setItemWidget(item, widget)
        finally:
            self.supplier_results.setUpdatesEnabled(True)
            self.supplier_results.viewport().update()

    def _recipient_row_widget(self, *, company: str, email: str, checked: bool, row: dict[str, str]) -> QWidget:
        # O card já nasce filho do viewport da lista. Criar QWidget sem parent
        # transforma o widget em top-level window no Windows e pode produzir
        # piscadas rápidas quando várias transportadoras são criadas de uma vez.
        wrapper = RecipientRowFrame(email=email, row=row, checked=checked, parent=self.supplier_results.viewport())
        # Calcula o estado no momento do clique, não no momento em que o card
        # foi criado. Isso evita inconsistência quando o usuário clica rápido
        # ou quando a lista ainda está repintando.
        wrapper.clicked.connect(self._toggle_recipient_from_card)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(scaled_px(10), scaled_px(7), scaled_px(10), scaled_px(7))
        layout.setSpacing(scaled_px(10))
        cb = QCheckBox(wrapper)
        cb.setChecked(checked)
        cb.setToolTip("Selecionar destinatário")
        cb.setProperty("recipientSelector", True)
        cb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        wrapper._recipient_checkbox = cb  # type: ignore[attr-defined]
        layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignVCenter)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(scaled_px(2))
        contact = clean_text(row.get("contato_nome"))
        product = clean_text(row.get("produto") or row.get("product") or row.get("categoria") or row.get("kind"))
        name_label = QLabel(company)
        name_label.setObjectName("recipientName")
        name_label.setToolTip(company)
        name_label.setWordWrap(False)
        # QLabel pode capturar o clique em alguns estilos/temas; deixa o
        # evento passar para o card pai, que alterna a seleção da linha inteira.
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta_parts = [part for part in [contact, email, product] if part]
        mail_label = QLabel("  •  ".join(meta_parts) if meta_parts else email)
        mail_label.setObjectName("recipientEmail")
        mail_label.setToolTip(" | ".join(meta_parts) if meta_parts else email)
        mail_label.setWordWrap(False)
        mail_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_box.addWidget(name_label)
        text_box.addWidget(mail_label)
        layout.addLayout(text_box, 1)
        btn_remove = QPushButton("×")
        btn_remove.setObjectName("iconRemove")
        btn_remove.setToolTip("Remover da seleção")
        btn_remove.setFixedWidth(scaled_px(32))
        btn_remove.setVisible(checked)
        btn_remove.clicked.connect(lambda _=False, e=email: self._remove_recipient(e))
        wrapper._recipient_remove_button = btn_remove  # type: ignore[attr-defined]
        layout.addWidget(btn_remove, 0, Qt.AlignmentFlag.AlignVCenter)
        return wrapper

    def _toggle_recipient_from_card(self, email: str, row_obj: object) -> None:
        row = row_obj if isinstance(row_obj, dict) else {}
        email_clean = clean_text(email or row.get("email", ""))
        key = normalize_text(email_clean)
        if not key:
            return
        if self._request_type == REQUEST_FREIGHT:
            checked = key in self._carrier_selected
        else:
            checked = key in self._selected_recipients
        self._toggle_recipient(email_clean, dict(row), not checked)

    def _on_supplier_result_item_clicked(self, item: QListWidgetItem) -> None:
        # Itens de frete são nativos (sem itemWidget) para evitar janelas órfãs.
        # Clicar no item inteiro marca/desmarca. Itens com widget próprio continuam
        # sendo tratados pelo RecipientRowFrame para não gerar clique duplo.
        if self._request_type != REQUEST_FREIGHT:
            return
        try:
            if self.supplier_results.itemWidget(item) is not None:
                return
        except Exception:
            pass
        self._toggle_recipient_item(item)

    def _toggle_recipient_item(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole) or {}
        if not isinstance(row, dict):
            return
        email = clean_text(row.get("email"))
        key = normalize_text(email)
        if self._request_type == REQUEST_FREIGHT:
            checked = key in self._carrier_selected
        else:
            checked = key in self._selected_recipients
        self._toggle_recipient(email, row, not checked)

    def _toggle_recipient(self, email: str, row: dict[str, str], checked: bool) -> None:
        email = clean_text(email)
        key = normalize_text(email)
        if not is_valid_email(email):
            return
        if self._request_type == REQUEST_FREIGHT:
            if checked:
                self._carrier_selected.add(key)
                if key not in self._default_freight_carrier_keys() and key not in self._extra_carriers:
                    self._extra_carriers[key] = {
                        "empresa": clean_text(row.get("empresa")) or self._company_from_email(email),
                        "email": email,
                        "contato_nome": clean_text(row.get("contato_nome")),
                        "telefone": clean_text(row.get("telefone")),
                        "produto": clean_text(row.get("produto")),
                        "kind": "supplier",
                        "kind_label": clean_text(row.get("produto")) or "Fornecedor da base",
                    }
            else:
                self._carrier_selected.discard(key)
        else:
            if checked:
                self._selected_recipients[key] = row
                self.app_context.state.selected_emails.add(email)
            else:
                self._selected_recipients.pop(key, None)
                self.app_context.state.selected_emails.discard(email)
        if self._request_type == REQUEST_FREIGHT:
            # Frete usa itens nativos; ao desmarcar uma transportadora selecionada
            # a lista precisa refletir imediatamente o conjunto atual. A operação é
            # pequena (padrão de 7) e não reconstrói prévia/assunto.
            self._recipient_visible_signature = tuple()
            self._refresh_supplier_suggestions()
        else:
            self._refresh_recipient_selection_state(target_key=key)
        # Selecionar/deselecionar destinatário não altera assunto, corpo ou botão de envio.
        # Evita reconstrução de prévia a cada clique e elimina microtravada perceptível.
        self._update_labels()

    def _refresh_recipient_selection_state(self, *, target_key: str | None = None) -> None:
        """Atualiza o estado visual dos cards visíveis com o menor trabalho possível.

        A troca de destinatário precisa parecer instantânea. Por isso, quando o
        clique vem de um card específico, atualizamos só esse card. A varredura
        completa fica reservada para refresh de lista/troca de modo.
        """
        selected_keys = self._carrier_selected if self._request_type == REQUEST_FREIGHT else self._selected_recipients.keys()
        update_all = not target_key
        found_target = False
        try:
            self.supplier_results.setUpdatesEnabled(False)
            for i in range(self.supplier_results.count()):
                item = self.supplier_results.item(i)
                row = item.data(Qt.ItemDataRole.UserRole) if item is not None else {}
                email = clean_text(row.get("email", "")) if isinstance(row, dict) else ""
                key = normalize_text(email)
                if target_key and key != target_key:
                    continue
                checked = key in selected_keys
                widget = self.supplier_results.itemWidget(item) if item is not None else None
                if widget is None:
                    continue
                self._apply_recipient_widget_state(widget, checked)
                found_target = True
                if not update_all:
                    break
        finally:
            self.supplier_results.setUpdatesEnabled(True)
            self.supplier_results.viewport().update()
        if target_key and not found_target:
            # Segurança para casos raros de lista recém-recriada entre clique e slot.
            self._refresh_recipient_selection_state()

    def _apply_recipient_widget_state(self, widget: QWidget, checked: bool) -> None:
        widget.setProperty("checked", "true" if checked else "false")
        cb = getattr(widget, "_recipient_checkbox", None)
        if cb is not None and cb.isChecked() != checked:
            cb.setChecked(checked)
        btn = getattr(widget, "_recipient_remove_button", None)
        if btn is not None:
            btn.setVisible(checked)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _remove_recipient(self, email: str) -> None:
        key = normalize_text(email)
        if self._request_type == REQUEST_FREIGHT:
            self._carrier_selected.discard(key)
            if key in self._extra_carriers:
                self._extra_carriers.pop(key, None)
            else:
                self._carrier_hidden_session.add(key)
        else:
            self._selected_recipients.pop(key, None)
            self.app_context.state.selected_emails.discard(clean_text(email))
        if self._request_type == REQUEST_FREIGHT:
            self._recipient_visible_signature = tuple()
            self._refresh_supplier_suggestions()
        else:
            self._refresh_recipient_selection_state(target_key=key)
        self._update_labels()

    def _search_recipient_rows(self, query: str) -> list[dict[str, str]]:
        # Usa cache materializado em memória para não reler contatos/local DB em cada tecla.
        candidates = self._all_recipient_candidate_rows()
        return search_recipient_rows(
            candidates,
            query,
            selected_emails=(self._carrier_selected if self._request_type == REQUEST_FREIGHT else self._selected_recipients.keys()),
            limit=12,
        )

    def _invalidate_recipient_cache(self) -> None:
        self._recipient_candidates_cache = None
        self._recipient_cache_generation += 1

    def _all_recipient_candidate_rows(self) -> list[dict[str, str]]:
        if self._recipient_candidates_cache is not None:
            return list(self._recipient_candidates_cache)
        rows: list[dict[str, str]] = []

        index = getattr(self.app_context.state, "index", None)
        suppliers: list[object] = []
        if index is not None:
            try:
                if hasattr(index, "get_all_suppliers"):
                    suppliers = list(index.get_all_suppliers() or [])
                elif hasattr(index, "suppliers"):
                    suppliers = list(getattr(index, "suppliers") or [])
            except Exception:
                suppliers = []
        for supplier in suppliers:
            rows.append(self._supplier_to_recipient(supplier))

        try:
            contacts = build_contact_index(self.app_context.state, getattr(self, "_thunderbird_contacts", []))
        except Exception:
            contacts = []
        for contact in contacts:
            rows.append({
                "empresa": clean_text(contact.company) or self._company_from_email(contact.email),
                "email": clean_text(contact.email),
                "contato_nome": clean_text(contact.contact_name),
                "telefone": "",
                "produto": "",
                "source": clean_text(getattr(contact, "source", "contact")) or "contact",
            })

        try:
            conn = connect_local_db()
            try:
                for local in get_all_local_suppliers(conn):
                    rows.append({
                        "empresa": clean_text(getattr(local, "empresa", "")) or self._company_from_email(getattr(local, "email", "")),
                        "email": clean_text(getattr(local, "email", "")),
                        "contato_nome": clean_text(getattr(local, "contato", "")),
                        "telefone": clean_text(getattr(local, "telefone", "")),
                        "produto": clean_text(getattr(local, "produto", "")) or clean_text(getattr(local, "product", "")) or clean_text(getattr(local, "categoria", "")),
                        "source": "local_supplier",
                    })
            finally:
                conn.close()
        except Exception:
            pass
        # Deduplica cedo para não renderizar contatos/histórico quando a base já tem
        # a mesma empresa/e-mail com produto e contato completos.
        deduped = [row.to_dict() for row in dedupe_recipient_rows(rows)]
        self._recipient_candidates_cache = deduped
        return list(deduped)

    def _recipient_match_score(self, q: str, row: dict[str, str]) -> int:
        return recipient_match_score(q, row)

    def _search_suppliers(self, query: str) -> list[object]:
        index = getattr(self.app_context.state, "index", None)
        if index is None:
            return []
        try:
            if clean_text(query) and hasattr(index, "search"):
                return list(index.search(query) or [])
            if hasattr(index, "get_all_suppliers"):
                return list(index.get_all_suppliers() or [])[:60]
            if hasattr(index, "suppliers"):
                return list(getattr(index, "suppliers") or [])[:60]
        except Exception:
            return []
        return []

    def _supplier_to_recipient(self, supplier: object) -> dict[str, str]:
        matched_items: list[object] = []
        if hasattr(supplier, "supplier"):
            matched_items = list(getattr(supplier, "matched_items", []) or [])
            supplier = getattr(supplier, "supplier")

        product_parts: list[str] = []
        for item in matched_items:
            name = clean_text(getattr(item, "item", "")) or clean_text(str(item))
            if name:
                product_parts.append(name)
        items = getattr(supplier, "items", None)
        if isinstance(items, list):
            for item in items[:6]:
                name = clean_text(getattr(item, "item", "")) or clean_text(str(item))
                if name:
                    product_parts.append(name)
        product_text = " | ".join(dict.fromkeys(product_parts))

        return {
            "empresa": clean_text(getattr(supplier, "empresa", "")) or clean_text(getattr(supplier, "company", "")) or clean_text(getattr(supplier, "name", "")) or "Fornecedor",
            "email": clean_text(getattr(supplier, "email", "")),
            "contato_nome": clean_text(getattr(supplier, "contato_nome", "")) or clean_text(getattr(supplier, "contato", "")) or clean_text(getattr(supplier, "contact", "")),
            "telefone": clean_text(getattr(supplier, "telefone", "")) or clean_text(getattr(supplier, "phone", "")),
            "produto": product_text or clean_text(getattr(supplier, "material_produto", "")) or clean_text(getattr(supplier, "produto", "")) or clean_text(getattr(supplier, "product", "")) or clean_text(getattr(supplier, "product_tags", "")) or clean_text(getattr(supplier, "categoria", "")),
            "source": "supplier_index",
        }

    def _add_manual_email(self) -> None:
        raw = clean_text(self.quick_search.text())
        emails = analyze_smart_input(raw).emails
        if not emails and is_valid_email(raw):
            emails = [raw]
        if not emails:
            QMessageBox.information(self, "Destinatário", "Digite ou cole um e-mail válido.")
            return
        for email in emails:
            self._add_email_recipient(email)
        with QSignalBlocker(self.quick_search):
            self.quick_search.clear()
        self._recipient_visible_signature = tuple()
        self._refresh_supplier_suggestions()
        self._refresh_all()

    def _add_email_recipient(self, email: str) -> None:
        email = clean_text(email).lower()
        if not is_valid_email(email):
            return
        key = normalize_text(email)
        if self._request_type == REQUEST_FREIGHT:
            self._extra_carriers[key] = {"empresa": self._company_from_email(email), "email": email, "contato_nome": "", "telefone": ""}
            self._carrier_selected.add(key)
            return
        supplier = None
        try:
            supplier = self.app_context.state.index.get_by_email(email)
        except Exception:
            supplier = None
        if supplier is not None:
            self._selected_recipients[key] = self._supplier_to_recipient(supplier)
            self.app_context.state.selected_emails.add(email)
            return
        default_company = self._company_from_email(email)
        company, ok = QInputDialog.getText(
            self,
            "Novo fornecedor",
            f"Este e-mail ainda não está cadastrado. Nome da empresa para salvar:\n{email}",
            text=default_company,
        )
        if not ok:
            company = default_company
        company = clean_text(company) or default_company
        try:
            ok_create, _message, row = self._supplier_edit_service.create_local_supplier(
                company=company,
                email=email,
                products_text=summarize_subject(strip_email_only_lines(self.smart_input.toPlainText())),
            )
            if ok_create and row is not None:
                self._selected_recipients[key] = {"empresa": row.company, "email": row.email, "contato_nome": row.contact, "telefone": row.phone}
                self._invalidate_recipient_cache()
            else:
                self._selected_recipients[key] = {"empresa": company, "email": email, "contato_nome": "", "telefone": ""}
        except Exception:
            self._selected_recipients[key] = {"empresa": company, "email": email, "contato_nome": "", "telefone": ""}
        self.app_context.state.selected_emails.add(email)

    def _company_from_email(self, email: str) -> str:
        domain = clean_text(email).split("@")[-1].split(".")[0] if "@" in email else "Fornecedor"
        return domain.replace("-", " ").replace("_", " ").title() or "Fornecedor"

    def _carrier_recipients(self) -> dict[str, dict[str, str]]:
        rows: dict[str, dict[str, str]] = {}
        for carrier in DEFAULT_FREIGHT_CARRIERS:
            key = normalize_text(carrier.email)
            if key not in self._carrier_selected:
                continue
            rows[key] = {"empresa": carrier.label, "email": carrier.email, "contato_nome": "", "telefone": ""}
        for key, row in self._extra_carriers.items():
            if key in self._carrier_selected:
                rows[key] = row
        return rows

    def _current_recipients(self) -> dict[str, dict[str, str]]:
        if self._request_type == REQUEST_FREIGHT:
            return self._carrier_recipients()
        return dict(self._selected_recipients)

    # ---------- Attachments / clipboard ----------
    def _pick_attachments(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Selecionar anexos", "", "Arquivos permitidos (*.pdf *.doc *.docx *.xls *.xlsx *.csv *.txt *.png *.jpg *.jpeg *.webp *.zip)")
        if filenames:
            self._add_attachments(filenames)

    def _add_attachments(self, paths: list[str]) -> None:
        added = 0
        errors: list[str] = []
        seen = {str(Path(p).resolve()).lower() for p in self._attachments if Path(p).exists()}
        for raw in paths:
            path = str(Path(raw))
            ok, message = validate_attachment_path(path)
            if not ok:
                errors.append(f"{Path(path).name}: {message}")
                continue
            resolved = str(Path(path).resolve()).lower()
            if resolved in seen:
                continue
            seen.add(resolved)
            self._attachments.append(path)
            added += 1
        if errors:
            QMessageBox.warning(self, "Anexos", "Alguns arquivos não foram anexados:\n" + "\n".join(errors[:6]))
        if added:
            self._set_status(f"{added} anexo(s) adicionado(s).")
        self._analyze_and_refresh()

    def _remove_attachment(self, path: str) -> None:
        target = str(path or "")
        if not target:
            return
        before = len(self._attachments)
        self._attachments = [item for item in self._attachments if str(item) != target]
        if len(self._attachments) != before:
            self._set_status("Anexo removido.")
        self._analyze_and_refresh()

    def _paste_from_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        paths: list[str] = []
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        if paths:
            self._add_attachments(paths)
            return
        if mime.hasText():
            text = mime.text()
            if self._request_type == REQUEST_MATERIAL:
                self.smart_input.insertPlainText(text)
            elif self._request_type == REQUEST_EX_PANELS:
                if not clean_text(self.ex_panel_name.text()):
                    self.ex_panel_name.setText(text.strip())
                else:
                    target = next((field for field in self.ex_spec_fields if not clean_text(field.text())), None)
                    if target is None:
                        self._add_ex_spec_field()
                        target = self.ex_spec_fields[-1]
                    target.setText(text.strip())
            elif self._request_type == REQUEST_FREIGHT:
                fields = parse_freight_fields(text)
                if fields.get("descricao") and not self.freight_desc.text().strip():
                    self.freight_desc.setText(fields.get("descricao", ""))
                if fields.get("volumes") and not self.freight_volumes.text().strip():
                    self.freight_volumes.setText(fields.get("volumes", ""))
                if fields.get("peso") and not self.freight_weight.text().strip():
                    self.freight_weight.setText(fields.get("peso", ""))
                if fields.get("valor_nf") and not self.freight_nf_value.text().strip():
                    self.freight_nf_value.setText(fields.get("valor_nf", ""))
                if fields.get("medidas") and not self.freight_measures.text().strip():
                    self.freight_measures.setText(fields.get("medidas", ""))
                if not any(fields.values()) and not self.freight_destination.text().strip():
                    self.freight_destination.setText(text.strip())
            elif self._request_type == REQUEST_PO:
                analysis = analyze_smart_input(text, self._attachments)
                if analysis.oc_number and not self.po_number.text().strip():
                    self.po_number.setText(analysis.oc_number)
                elif text.strip() and not self.po_supplier_hint.text().strip():
                    self.po_supplier_hint.setText(text.strip())
            self._refresh_all()
            self._refresh_supplier_suggestions()
            return
        QMessageBox.information(self, "Colar", "Não encontrei texto ou arquivo na área de transferência.")

    def _smart_paste_shortcut(self) -> None:
        focused = QApplication.focusWidget()
        if focused is self.smart_input and hasattr(focused, "paste"):
            focused.paste()
            return
        if isinstance(focused, QLineEdit):
            focused.paste()
            return
        self._paste_from_clipboard()

    # ---------- Validation / send ----------
    def _validation_warnings(self, *, soft: bool = False) -> list[str]:
        warnings: list[str] = []
        recipients = self._current_recipients()
        if not dedupe_emails(row.get("email", "") for row in recipients.values()):
            warnings.append("sem destinatário")
        if self._request_type == REQUEST_MATERIAL and self._custom_quote_type:
            vals = self._custom_field_values()
            missing_fields = []
            for f in self._custom_type_fields():
                var = str(f.get("var") or "").upper()
                if bool(f.get("required", True)) and not clean_text(vals.get(var, "")):
                    missing_fields.append(str(f.get("label") or var))
            if missing_fields:
                warnings.append("sem " + ", ".join(missing_fields[:3]))
        elif self._request_type == REQUEST_MATERIAL and not strip_email_only_lines(self.smart_input.toPlainText()):
            warnings.append("sem material para cotar")
        if self._request_type == REQUEST_EX_PANELS and not self._ex_panels:
            warnings.append("sem painel cadastrado")
        if self._request_type == REQUEST_FREIGHT:
            missing = []
            for text, label in (
                (self.freight_desc.text(), "descrição"),
                (self.freight_volumes.text(), "volumes"),
                (self.freight_weight.text(), "peso"),
                (self.freight_nf_value.text(), "valor da NF"),
                (self.freight_measures.text(), "medidas"),
            ):
                if not clean_text(text):
                    missing.append(label)
            if missing:
                warnings.append("frete sem " + ", ".join(missing))
        if self._request_type == REQUEST_PO:
            if not clean_text(self.po_number.text()):
                warnings.append("sem número da OC")
            if not self._attachments:
                warnings.append("sem anexo")
        return warnings

    def _signature_html(self) -> str:
        owner = clean_text(self.signature_combo.currentText()) or first_signature_owner(self.app_context.state.config)
        cfg = self.app_context.state.config
        profile_key = clean_text(cfg.smtp_active_profile)
        profile = cfg.get_active_profile()
        profile_label = clean_text(getattr(profile, "label", ""))
        path = resolve_signature_html_path(owner, profile_key, profile_label)
        if not path:
            return ""
        if path not in self._signature_cache:
            self._signature_cache[path] = load_signature_html(path)
        return self._signature_cache[path]

    def _signature_plain(self) -> str:
        raw = self._signature_html()
        if not raw:
            return ""
        text = re.sub(r"(?i)<br\s*/?>", "\n", raw)
        text = re.sub(r"(?i)</(p|div|tr|li|table|h[1-6])>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_lib.unescape(text)
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)

    def _body_with_signature_text(self, body: str) -> str:
        sig = self._signature_plain()
        body = str(body or "").rstrip()
        if not sig:
            return body
        return body + "\n\n-- \n" + sig

    def _make_tracking_ref(self) -> str:
        return f"CV-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}"

    def _body_with_tracking_ref(self, body: str, tracking_ref: str) -> str:
        ref = clean_text(tracking_ref)
        if not ref:
            return body
        return str(body or "").rstrip() + f"\n\nRef. interna: {ref}"

    def _preview_html(self, body: str) -> str:
        safe_body = html_lib.escape(str(body or "")).replace("\n", "<br>")
        sig_html = self._signature_html()
        sig_block = f"<hr><div>{sig_html}</div>" if sig_html else "<hr><div><b>Assinatura:</b> não configurada</div>"
        return f"<div style='font-family: Segoe UI, Arial; font-size: 13px; line-height: 1.45'>{safe_body}{sig_block}</div>"

    def _send_current_request(self, *, confirm_review: bool = True) -> None:
        if self._request_type == REQUEST_SUPPLIERS:
            self._open_material_from_suppliers()
            return
        if self._sending:
            return
        recipients_map = self._current_recipients()
        recipients = dedupe_emails(row.get("email", "") for row in recipients_map.values())
        if not recipients:
            QMessageBox.warning(self, "Envio", "Selecione ou adicione pelo menos um destinatário válido.")
            return
        subject, base_body, _button_text = self._build_current_email()
        tracking_ref = self._make_tracking_ref()
        tracked_body = self._body_with_tracking_ref(base_body, tracking_ref)
        body = self._body_with_signature_text(tracked_body).strip()
        if not subject or not body:
            QMessageBox.warning(self, "Envio", "Confira o assunto e o corpo do e-mail antes de enviar.")
            return
        warnings = self._validation_warnings(soft=False)
        blocking = [w for w in warnings if w in {"sem material para cotar", "sem painel cadastrado"}]
        if blocking:
            QMessageBox.warning(self, "Envio", "Antes de enviar: " + ", ".join(blocking) + ".")
            return
        if warnings:
            answer = QMessageBox.question(
                self,
                "Enviar com pendências?",
                "O app encontrou estes pontos:\n- " + "\n- ".join(warnings) + "\n\nDeseja enviar mesmo assim?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        cfg = self.app_context.state.config
        cfg.smtp_active_profile = company_for_key(self._company_key).smtp_profile
        try:
            cfg.save()
        except Exception:
            pass
        profile = cfg.get_active_profile()
        if profile is None:
            QMessageBox.warning(self, "SMTP", "Nenhum perfil de envio configurado.")
            return
        password = get_password_from_profile(profile, allow_prompt=True)
        if not password:
            QMessageBox.warning(self, "SMTP", "Não foi possível obter a senha do e-mail de envio.")
            return
        sig_html = self._signature_html()
        body_html = build_html_email_body(tracked_body, sig_html) if sig_html else ""
        if confirm_review:
            if QMessageBox.question(
                self,
                "Confirmar envio",
                f"Enviar separado para {len(recipients)} destinatário(s) usando {profile.from_email}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        self._sending = True
        self.btn_send.setEnabled(False)
        self.btn_send.setText("Enviando...")
        attachments = list(self._attachments)
        signals = _SendSignals(self)
        signals.progress.connect(self._on_send_progress)
        signals.done.connect(lambda result: self._on_send_done(result, recipients_map, subject, body, attachments, tracking_ref))
        signals.error.connect(self._on_send_error)
        worker = _SendRunnable(
            app_context=self.app_context,
            recipients=recipients,
            subject=subject,
            body=body,
            body_html=body_html,
            password=password,
            attachments=attachments,
            signals=signals,
            tracking_id=tracking_ref,
        )
        self._thread_pool.start(worker)

    def _on_send_progress(self, done: int, total: int, recipient: str) -> None:
        self._set_status(f"Enviando {done}/{total}: {recipient}")

    def _on_send_done(self, result: object, recipients_map: dict[str, dict[str, str]], subject: str, body: str, attachments: list[str], tracking_ref: str = "") -> None:
        self._sending = False
        self.btn_send.setEnabled(True)
        self.btn_send.setText(self._build_current_email()[2])
        success = bool(getattr(result, "success", False))
        failed = list(getattr(result, "failed_emails", []) or [])
        message = clean_text(getattr(result, "message", ""))
        self._record_history(success=success, recipients_map=recipients_map, subject=subject, body=body, failed=failed, attachments=attachments, tracking_ref=tracking_ref)
        if success:
            QMessageBox.information(self, "Envio", message or "E-mails enviados com sucesso.")
            self._set_status(message or "Envio concluído.")
            if failed:
                try:
                    enqueue_email(profile_key=company_for_key(self._company_key).smtp_profile, recipients=failed, subject=subject, body=body, attachments=attachments, tracking_id=tracking_ref, error=message)
                    self._set_status("Falhas parciais salvas na fila de reenvio.")
                except Exception:
                    pass
        else:
            try:
                all_emails = dedupe_emails(row.get("email", "") for row in recipients_map.values())
                enqueue_email(profile_key=company_for_key(self._company_key).smtp_profile, recipients=failed or all_emails, subject=subject, body=body, attachments=attachments, tracking_id=tracking_ref, error=message)
                QMessageBox.warning(self, "Envio", (message or "Falha no envio.") + "\n\nO envio foi salvo na fila e o app tentará novamente automaticamente.")
                self._set_status("Falha salva na fila de reenvio automático.")
            except Exception:
                QMessageBox.warning(self, "Envio", message or "Falha no envio.")
                self._set_status(message or "Falha no envio.")

    def _on_send_error(self, error: str) -> None:
        self._sending = False
        self.btn_send.setEnabled(True)
        self.btn_send.setText(self._build_current_email()[2])
        QMessageBox.warning(self, "Envio", f"Falha inesperada: {error}")
        self._set_status(f"Falha inesperada no envio: {error}")

    def _record_history(self, *, success: bool, recipients_map: dict[str, dict[str, str]], subject: str, body: str, failed: list[str], attachments: list[str], tracking_ref: str = "") -> None:
        history = getattr(self.app_context.state, "history", None)
        if history is None:
            return
        custom_name = str(self._custom_quote_type.get("name") if self._custom_quote_type else "")
        request_label = {
            REQUEST_MATERIAL: "cotacao_material",
            REQUEST_EX_PANELS: "cotacao_paineis_ex",
            REQUEST_FREIGHT: "cotacao_frete",
            REQUEST_PO: "ordem_compra",
        }.get(self._request_type, "solicitacao")
        if self._custom_quote_type:
            request_label = "custom_" + str(self._custom_quote_type.get("id") or "envio")
        try:
            history.record_send_event(
                status="sent" if success and not failed else "partial_or_failed",
                product_query=self._history_product_query(),
                subject=subject,
                body=body,
                recipients=list(recipients_map.values()),
                items=self._history_items(),
                failed_emails=failed,
                event_type=request_label,
                extra={
                    "company_key": self._company_key,
                    "signature_owner": clean_text(self.signature_combo.currentText()),
                    "request_type": request_label,
                    "attachments": attachments,
                    "rfq_id": clean_text(tracking_ref),
                    "tracking_ref": clean_text(tracking_ref),
                    "smart_composer": True,
                    "custom_quote_type": self._custom_quote_type or {},
                    "visual_version": "2.3.0",
                },
            )
        except Exception:
            pass

    def _history_product_query(self) -> str:
        if self._request_type == REQUEST_EX_PANELS:
            return "Painéis Elétricos Ex"
        if self._request_type == REQUEST_FREIGHT:
            return self.freight_desc.text().strip() or "Cotação de frete"
        if self._request_type == REQUEST_PO:
            return f"OC {self.po_number.text().strip()}".strip()
        if self._custom_quote_type:
            return summarize_subject(self._custom_field_values().get("CONTEUDO", "")) or str(self._custom_quote_type.get("name") or "Envio")
        return summarize_subject(strip_email_only_lines(self.smart_input.toPlainText()))

    def _history_items(self) -> list[str]:
        if self._request_type == REQUEST_EX_PANELS:
            return [line.strip() for line in self._format_ex_panels_text().splitlines() if line.strip()]
        if self._request_type == REQUEST_FREIGHT:
            return [line for line in [
                f"Descrição: {self.freight_desc.text().strip()}",
                f"Volumes: {self.freight_volumes.text().strip()}",
                f"Peso: {self.freight_weight.text().strip()}",
                f"Valor NF: {self.freight_nf_value.text().strip()}",
                f"Medidas: {self.freight_measures.text().strip()}",
            ] if line.split(":", 1)[1].strip()]
        if self._request_type == REQUEST_PO:
            return [f"OC {self.po_number.text().strip()}"] if self.po_number.text().strip() else []
        if self._custom_quote_type:
            values = self._custom_field_values()
            return [f"{f.get('label')}: {values.get(str(f.get('var') or '').upper(), '')}" for f in self._custom_type_fields() if values.get(str(f.get('var') or '').upper(), '')]
        return [line.strip() for line in strip_email_only_lines(self.smart_input.toPlainText()).splitlines() if line.strip()]

    def _clear_composer(self) -> None:
        self._request_type_was_manual = False
        self.smart_input.clear()
        for widget in getattr(self, "_custom_field_widgets", {}).values():
            if isinstance(widget, QTextEdit):
                widget.clear()
            elif isinstance(widget, QLineEdit):
                widget.clear()
        self.quick_search.clear()
        self.freight_desc.clear()
        self.freight_volumes.clear()
        self.freight_weight.clear()
        self.freight_nf_value.clear()
        self.freight_measures.clear()
        self.freight_destination.clear()
        self.po_number.clear()
        self.po_supplier_hint.clear()
        self.ex_required_check.setChecked(False)
        self._ex_panels.clear()
        self._clear_ex_panel_form()
        self._refresh_ex_panels_list()
        self._selected_recipients.clear()
        self.app_context.state.selected_emails.clear()
        self._extra_carriers.clear()
        self._carrier_selected = set()
        self._carrier_hidden_session.clear()
        self._attachments.clear()
        self._auto_emails_added.clear()
        self._set_request_type(REQUEST_MATERIAL, manual=False)
        self._analyze_and_refresh()

    def _set_status(self, message: str) -> None:
        if self._on_status:
            self._on_status(str(message or ""))


# Métodos públicos usados pelo shell lateral v1.6
def set_request_type_public(self, request_type: str) -> None:
    self._set_request_type(request_type, manual=True)

def set_company_key_public(self, company_key: str) -> None:
    self._set_company(company_key)

def set_signature_owner_public(self, owner: str) -> None:
    try:
        idx = self.signature_combo.findText(owner)
        if idx >= 0:
            self.signature_combo.setCurrentIndex(idx)
    except Exception:
        pass

def signature_owner_options_public(self) -> list[str]:
    try:
        return [self.signature_combo.itemText(i) for i in range(self.signature_combo.count())]
    except Exception:
        return []

# Bind helper functions to the class for the new dashboard shell.
try:
    NewRequestPage.set_request_type_public = set_request_type_public  # type: ignore[name-defined, assignment]
    NewRequestPage.set_company_key_public = set_company_key_public  # type: ignore[name-defined, assignment]
    NewRequestPage.set_signature_owner_public = set_signature_owner_public  # type: ignore[name-defined, assignment]
    NewRequestPage.signature_owner_options_public = signature_owner_options_public  # type: ignore[name-defined, assignment]
except NameError:
    pass
