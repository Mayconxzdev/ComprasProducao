from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.application.context import AppContext
from app.core.bootstrap_runtime import ensure_runtime_bootstrap
from app.core.cache_manager import get_cache_file_path
from app.core.config import AppConfig
from app.core.config_sync import save_to_master
from app.core.config_transfer import export_config, get_default_export_filename, import_config
from app.core.path_utils import first_existing_nas_path, nas_fallback_candidates, normalize_master_path
from app.core.utils_text import normalize_text
from app.core.imap_monitor import get_imap_password, set_imap_password, sync_inbox_replies
from app.qt.ui_scale import font_css
from app.qt.widgets.smart_suggest_line_edit import (
    SmartSuggestLineEdit,
    SuggestionOption,
    resolve_suggestions,
)

from .smtp_dialog import SMTPConfigDialog


class SettingsPage(QWidget):
    def __init__(
        self,
        app_context: AppContext,
        *,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.app_context = app_context
        self._on_status = on_status
        self._clear_brave_key_requested = False
        self._on_install_update: Callable[[], None] | None = None
        self._on_retry_download: Callable[[], None] | None = None
        self._update_state = "idle"
        self._update_payload: dict[str, Any] = {}

        self._build_ui()
        self._load_from_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        title = QLabel("Configurações")
        title.setStyleSheet(font_css(20, 700))
        root.addWidget(title)

        subtitle = QLabel("Base de dados, contas de envio, assinaturas e integração com o servidor.")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        content = QWidget(scroll)
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        scroll.setWidget(content)

        # Tela comum: somente o que uma pessoa de compras precisa entender.
        self._content_layout.addWidget(self._build_start_here_block())
        self._content_layout.addWidget(self._build_signatures_block())
        self._content_layout.addWidget(self._build_xlsx_block())
        self._content_layout.addWidget(self._build_history_block())

        self.btn_advanced_toggle = QPushButton("Mostrar configurações avançadas")
        self.btn_advanced_toggle.setObjectName("secondarySmall")
        self.btn_advanced_toggle.clicked.connect(self._toggle_advanced_settings)
        self._content_layout.addWidget(self.btn_advanced_toggle)

        self.advanced_container = QFrame(self)
        self.advanced_container.setObjectName("advancedSettingsPanel")
        advanced_layout = QVBoxLayout(self.advanced_container)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(12)

        # Preferências técnicas, caminhos de rede e diagnósticos no avançado
        advanced_layout.addWidget(self._build_paths_block())
        advanced_layout.addWidget(self._build_smtp_block())
        advanced_layout.addWidget(self._build_imap_block())
        advanced_layout.addWidget(self._build_misc_block())
        advanced_layout.addWidget(self._build_data_diag_block())
        advanced_layout.addWidget(self._build_update_block())
        advanced_layout.addStretch(1)
        self.advanced_container.setVisible(False)
        self._content_layout.addWidget(self.advanced_container)
        self._content_layout.addStretch(1)

    def _build_block(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame(self)
        frame.setObjectName("pageCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setStyleSheet(font_css(15, 600))
        layout.addWidget(title_label)
        return frame, layout

    def _build_start_here_block(self) -> QFrame:
        frame, layout = self._build_block("Essencial")
        info = QLabel(
            "Aqui ficam apenas as opções que afetam o uso diário: assinatura, contas de envio, respostas recebidas e histórico. "
            "Caminhos, diagnóstico e planilhas ficam em Configurações avançadas."
        )
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        row = QHBoxLayout()
        btn_sig = QPushButton("Assinaturas")
        btn_sig.clicked.connect(self._open_signature_dialog)
        row.addWidget(btn_sig)
        btn_smtp = QPushButton("Contas de envio")
        btn_smtp.clicked.connect(self._open_smtp_dialog)
        row.addWidget(btn_smtp)
        btn_sync = QPushButton("Sincronizar respostas")
        btn_sync.setObjectName("accent")
        btn_sync.clicked.connect(self._test_imap_now)
        row.addWidget(btn_sync)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _toggle_advanced_settings(self) -> None:
        visible = not bool(self.advanced_container.isVisible())
        self.advanced_container.setVisible(visible)
        self.btn_advanced_toggle.setText("Ocultar configurações avançadas" if visible else "Mostrar configurações avançadas")

    def _build_xlsx_block(self) -> QFrame:
        frame, layout = self._build_block("Base de fornecedores")

        self.xlsx_list = QListWidget(frame)
        self.xlsx_list.setMinimumHeight(120)
        layout.addWidget(self.xlsx_list)

        row = QHBoxLayout()
        self.btn_add_xlsx = QPushButton("Adicionar XLSX")
        self.btn_add_xlsx.clicked.connect(self._add_xlsx)
        row.addWidget(self.btn_add_xlsx)

        self.btn_remove_xlsx = QPushButton("Remover selecionado")
        self.btn_remove_xlsx.clicked.connect(self._remove_selected_xlsx)
        row.addWidget(self.btn_remove_xlsx)
        row.addStretch(1)
        layout.addLayout(row)

        return frame

    def _build_paths_block(self) -> QFrame:
        frame, layout = self._build_block("Local da base e exportações")

        layout.addWidget(QLabel("Arquivo principal de fornecedores:"))
        nas_row = QHBoxLayout()
        self.nas_edit = QLineEdit(frame)
        nas_row.addWidget(self.nas_edit, 1)
        self.btn_test_nas = QPushButton("Testar acesso")
        self.btn_test_nas.clicked.connect(self._test_nas_path)
        nas_row.addWidget(self.btn_test_nas)
        layout.addLayout(nas_row)

        layout.addWidget(QLabel("Pasta para relatórios e histórico:"))
        export_row = QHBoxLayout()
        self.export_edit = QLineEdit(frame)
        export_row.addWidget(self.export_edit, 1)
        btn_choose_export = QPushButton("Escolher")
        btn_choose_export.clicked.connect(self._pick_export_dir)
        export_row.addWidget(btn_choose_export)
        layout.addLayout(export_row)

        layout.addWidget(QLabel("Thunderbird instalado (opcional):"))
        tb_row = QHBoxLayout()
        self.thunderbird_edit = QLineEdit(frame)
        tb_row.addWidget(self.thunderbird_edit, 1)
        btn_choose_tb = QPushButton("Procurar")
        btn_choose_tb.clicked.connect(self._pick_thunderbird_path)
        tb_row.addWidget(btn_choose_tb)
        layout.addLayout(tb_row)

        return frame

    def _build_signatures_block(self) -> QFrame:
        frame, layout = self._build_block("Assinaturas de E-mail")

        info = QLabel("Gerencie as assinaturas HTML de cada usuário para as cotações.")
        info.setObjectName("muted")
        layout.addWidget(info)

        row = QHBoxLayout()
        btn_manage = QPushButton("Gerenciar Assinaturas")
        btn_manage.clicked.connect(self._open_signature_dialog)
        row.addWidget(btn_manage)
        row.addStretch(1)
        layout.addLayout(row)

        return frame

    def _build_web_block(self) -> QFrame:
        frame, layout = self._build_block("Busca Web")

        layout.addWidget(QLabel("Provedor principal:"))
        provider_row = QHBoxLayout()
        self.web_provider_field = SmartSuggestLineEdit(frame, debounce_ms=150, allow_manual=False)
        self.web_provider_field.set_provider(self._provider_web_provider)
        provider_row.addWidget(self.web_provider_field, 1)
        self.web_hint = QLabel("")
        self.web_hint.setObjectName("muted")
        provider_row.addWidget(self.web_hint)
        layout.addLayout(provider_row)

        layout.addWidget(QLabel("Brave API Key (oculta):"))
        key_row = QHBoxLayout()
        self.brave_key_edit = QLineEdit(frame)
        self.brave_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_row.addWidget(self.brave_key_edit, 1)
        btn_clear_key = QPushButton("Limpar chave")
        btn_clear_key.clicked.connect(self._clear_brave_key)
        key_row.addWidget(btn_clear_key)
        layout.addLayout(key_row)

        self.brave_mask_label = QLabel("Atual: (não configurada)")
        self.brave_mask_label.setObjectName("muted")
        layout.addWidget(self.brave_mask_label)

        return frame

    def _build_data_diag_block(self) -> QFrame:
        frame, layout = self._build_block("Saúde da base")

        self.lbl_diag_sync = QLabel("Sync master: -")
        self.lbl_diag_sync.setObjectName("muted")
        layout.addWidget(self.lbl_diag_sync)

        self.lbl_diag_bootstrap = QLabel("Bootstrap local: -")
        self.lbl_diag_bootstrap.setObjectName("muted")
        layout.addWidget(self.lbl_diag_bootstrap)

        self.lbl_diag_cache = QLabel("Cache XLSX: -")
        self.lbl_diag_cache.setObjectName("muted")
        layout.addWidget(self.lbl_diag_cache)

        self.lbl_diag_nas = QLabel("NAS XLSX: -")
        self.lbl_diag_nas.setObjectName("muted")
        layout.addWidget(self.lbl_diag_nas)

        row = QHBoxLayout()
        self.btn_diag_refresh = QPushButton("Validar agora")
        self.btn_diag_refresh.clicked.connect(self._refresh_data_diagnostics)
        row.addWidget(self.btn_diag_refresh)

        self.btn_diag_repair = QPushButton("Reparar base agora")
        self.btn_diag_repair.clicked.connect(self._repair_data_bootstrap)
        row.addWidget(self.btn_diag_repair)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _build_misc_block(self) -> QFrame:
        frame, layout = self._build_block("Preferências")

        layout.addWidget(QLabel("Prefixo padrao do assunto:"))
        self.subject_prefix_edit = QLineEdit(frame)
        layout.addWidget(self.subject_prefix_edit)

        layout.addWidget(QLabel("Aba da planilha (normalmente Fornecedores):"))
        self.sheet_name_edit = QLineEdit(frame)
        layout.addWidget(self.sheet_name_edit)

        transfer_row = QHBoxLayout()
        btn_export_cfg = QPushButton("Exportar configuracao")
        btn_export_cfg.clicked.connect(self._export_config)
        transfer_row.addWidget(btn_export_cfg)

        btn_import_cfg = QPushButton("Importar configuracao")
        btn_import_cfg.clicked.connect(self._import_config)
        transfer_row.addWidget(btn_import_cfg)
        transfer_row.addStretch(1)
        layout.addLayout(transfer_row)

        return frame

    def _build_smtp_block(self) -> QFrame:
        frame, layout = self._build_block("Contas de envio")

        self.chk_hidden_bcc = QCheckBox("Neste PC, adicionar cópias ocultas definidas pela política local")
        layout.addWidget(self.chk_hidden_bcc)
        smtp_hint = QLabel("Essa regra vale só para este computador e não é sincronizada pelo NAS.")
        smtp_hint.setObjectName("muted")
        layout.addWidget(smtp_hint)

        smtp_row = QHBoxLayout()
        btn_smtp = QPushButton("Configurar SMTP")
        btn_smtp.clicked.connect(self._open_smtp_dialog)
        smtp_row.addWidget(btn_smtp)

        self.btn_save = QPushButton("Salvar configurações")
        self.btn_save.setObjectName("accent")
        self.btn_save.clicked.connect(self._save)
        smtp_row.addWidget(self.btn_save)
        smtp_row.addStretch(1)
        layout.addLayout(smtp_row)
        return frame

    def _build_imap_block(self) -> QFrame:
        frame, layout = self._build_block("Respostas recebidas")

        info = QLabel("O app pode ler caixas IMAP configuradas pelo operador para atualizar Acompanhar automaticamente.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.chk_imap_auto = QCheckBox("Verificar respostas ao abrir Acompanhar")
        layout.addWidget(self.chk_imap_auto)

        self.imap_widgets: dict[str, dict[str, QWidget]] = {}
        for key, label, default_user in (
            ("vesper", "Empresa A", "compras@empresa-a.invalid"),
            ("ventrio", "Empresa B", "compras@empresa-b.invalid"),
        ):
            card = QFrame(frame)
            card.setObjectName("subtlePanel")
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 8, 10, 8)
            enabled = QCheckBox(label)
            enabled.setChecked(True)
            row.addWidget(enabled, 0)
            user = QLineEdit(card)
            user.setPlaceholderText(default_user)
            row.addWidget(user, 2)
            password = QLineEdit(card)
            password.setEchoMode(QLineEdit.EchoMode.Password)
            password.setPlaceholderText("Senha IMAP (deixe vazio para manter/reusar SMTP)")
            row.addWidget(password, 2)
            self.imap_widgets[key] = {"enabled": enabled, "user": user, "password": password}
            layout.addWidget(card)

        row = QHBoxLayout()
        btn_test = QPushButton("Testar e sincronizar respostas")
        btn_test.clicked.connect(self._test_imap_now)
        row.addWidget(btn_test)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _build_update_block(self) -> QFrame:
        frame, layout = self._build_block("Atualização")

        self.lbl_update_current = QLabel("Versão atual: -")
        self.lbl_update_current.setObjectName("muted")
        layout.addWidget(self.lbl_update_current)

        self.lbl_update_available = QLabel("Versão disponível: -")
        self.lbl_update_available.setObjectName("muted")
        layout.addWidget(self.lbl_update_available)

        self.lbl_update_progress = QLabel("Progresso: -")
        self.lbl_update_progress.setObjectName("muted")
        layout.addWidget(self.lbl_update_progress)

        self.lbl_update_status = QLabel("Status: ocioso")
        self.lbl_update_status.setWordWrap(True)
        layout.addWidget(self.lbl_update_status)

        actions = QHBoxLayout()
        self.btn_update_action = QPushButton("Ações de atualização")
        self.btn_update_action.setAccessibleName("Baixar ou instalar atualização do programa")
        self.btn_update_action.setToolTip("Aparece quando houver atualização para baixar ou instalar.")
        self.btn_update_action.clicked.connect(self._on_update_action_clicked)
        self.btn_update_action.hide()
        actions.addWidget(self.btn_update_action)
        actions.addStretch(1)
        layout.addLayout(actions)
        return frame

    def _build_history_block(self) -> QFrame:
        frame, layout = self._build_block("Acompanhar e histórico")

        actions = QHBoxLayout()
        self.btn_export_history_now = QPushButton("Exportar histórico agora")
        self.btn_export_history_now.clicked.connect(self._export_history_now)
        actions.addWidget(self.btn_export_history_now)

        self.btn_clear_history = QPushButton("Limpar histórico")
        self.btn_clear_history.clicked.connect(self._clear_history_with_archive)
        actions.addWidget(self.btn_clear_history)
        actions.addStretch(1)

        layout.addLayout(actions)
        hint = QLabel("Limpar histórico arquiva os arquivos atuais antes de limpar.")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        return frame

    def _provider_web_provider(self, query: str, force: bool) -> list[SuggestionOption]:
        options = [
            SuggestionOption(label="auto", value="auto"),
            SuggestionOption(label="brave", value="brave"),
            SuggestionOption(label="google", value="google"),
        ]
        return resolve_suggestions(options, query, force=force, limit=12)

    def _web_provider_value(self) -> str:
        if not hasattr(self, "web_provider_field"):
            return "disabled"
        value = normalize_text(self.web_provider_field.value())
        if value in {"disabled", "auto", "brave", "google"}:
            return value
        return "disabled"

    def _load_from_state(self) -> None:
        cfg = self.app_context.state.config

        self.xlsx_list.clear()
        for source in cfg.xlsx_sources:
            self.xlsx_list.addItem(QListWidgetItem(source))

        self.nas_edit.setText(cfg.nas_master_path or "")
        self.export_edit.setText(cfg.export_history_default_dir or "")
        self.thunderbird_edit.setText(cfg.thunderbird_path or "")
        self.subject_prefix_edit.setText(cfg.default_subject_prefix or "Cotação")
        self.sheet_name_edit.setText(cfg.xlsx_sheet_name or "Fornecedores")
        if hasattr(self, "web_provider_field"):
            self.web_provider_field.set_value(cfg.web_search.primary_provider or "disabled")
            masked_key = cfg.web_search.masked_brave_api_key()
            if masked_key:
                self.brave_mask_label.setText(f"Atual: {masked_key}")
            else:
                self.brave_mask_label.setText("Atual: (não configurada)")
            if hasattr(self, "brave_key_edit"):
                self.brave_key_edit.clear()
        self.chk_hidden_bcc.setChecked(bool(getattr(cfg, "pc_hidden_bcc_enabled", False)))
        if hasattr(self, "chk_imap_auto"):
            self.chk_imap_auto.setChecked(bool(getattr(cfg, "imap_check_on_open_history", False)))
        if hasattr(self, "imap_widgets"):
            cfg.ensure_imap_profiles()
            for key, widgets in self.imap_widgets.items():
                profile = cfg.imap_profiles.get(key)
                if profile is None:
                    continue
                enabled = widgets.get("enabled")
                user = widgets.get("user")
                password = widgets.get("password")
                if isinstance(enabled, QCheckBox):
                    enabled.setChecked(bool(profile.enabled))
                if isinstance(user, QLineEdit):
                    user.setText(str(profile.username or ""))
                if isinstance(password, QLineEdit):
                    password.clear()
                    password.setPlaceholderText("Senha salva" if get_imap_password(cfg, profile) else "Senha IMAP (ou reusar SMTP)")
        self._clear_brave_key_requested = False
        self.set_update_status(self._update_state, dict(self._update_payload))
        self._refresh_data_diagnostics()

    def bind_update_actions(
        self,
        *,
        on_install_update: Callable[[], None] | None = None,
        on_retry_download: Callable[[], None] | None = None,
    ) -> None:
        self._on_install_update = on_install_update
        self._on_retry_download = on_retry_download

    def set_update_status(self, state: str, payload: dict[str, Any] | None = None) -> None:
        state_name = str(state or "idle")
        payload_dict = dict(payload or {})
        self._update_state = state_name
        self._update_payload = payload_dict

        current = str(payload_dict.get("current_version") or "-")
        latest = str(payload_dict.get("latest_version") or "-")
        progress = int(payload_dict.get("progress") or 0)
        message = str(payload_dict.get("message") or "").strip()

        self.lbl_update_current.setText(f"Versão atual: {current}")
        self.lbl_update_available.setText(f"Versão disponível: {latest}")
        self.lbl_update_progress.setText("Progresso: -" if progress <= 0 else f"Progresso: {progress}%")

        self.btn_update_action.hide()
        self.btn_update_action.setEnabled(True)
        self.btn_update_action.setText("Ações de atualização")

        if state_name == "checking":
            self.lbl_update_status.setText("Status: verificando atualizações...")
            return
        if state_name == "available_downloading":
            self.lbl_update_status.setText("Status: atualização disponível, baixando em segundo plano.")
            self.btn_update_action.setText("Baixando...")
            self.btn_update_action.setEnabled(False)
            self.btn_update_action.show()
            return
        if state_name == "ready_to_install":
            self.lbl_update_status.setText("Status: atualização pronta para instalar.")
            self.btn_update_action.setText("Instalar e reiniciar")
            self.btn_update_action.show()
            return
        if state_name == "error_download":
            details = f" {message}" if message else ""
            self.lbl_update_status.setText(f"Status: falha ao baixar atualização.{details}")
            self.btn_update_action.setText("Baixar novamente")
            self.btn_update_action.show()
            return
        if state_name == "installing":
            self.lbl_update_status.setText("Status: instalando atualização...")
            self.btn_update_action.setText("Instalando...")
            self.btn_update_action.setEnabled(False)
            self.btn_update_action.show()
            return
        if state_name == "idle" and message == "up_to_date":
            self.lbl_update_status.setText("Status: app atualizado.")
            return
        if state_name == "idle" and message:
            self.lbl_update_status.setText(f"Status: {message}")
            return
        self.lbl_update_status.setText("Status: ocioso")

    def _on_update_action_clicked(self) -> None:
        if self._update_state == "ready_to_install":
            if callable(self._on_install_update):
                self._on_install_update()
            return
        if self._update_state == "error_download":
            if callable(self._on_retry_download):
                self._on_retry_download()
            return

    def _add_xlsx(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Adicionar XLSX",
            "",
            "Excel (*.xlsx)",
        )
        if not paths:
            return
        existing = {self.xlsx_list.item(i).text() for i in range(self.xlsx_list.count())}
        for path in paths:
            if path not in existing:
                self.xlsx_list.addItem(QListWidgetItem(path))

    def _remove_selected_xlsx(self) -> None:
        row = self.xlsx_list.currentRow()
        if row < 0:
            self._warn("XLSX", "Selecione um item para remover.")
            return
        self.xlsx_list.takeItem(row)

    def _pick_export_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Escolha diretorio de export")
        if folder:
            self.export_edit.setText(folder)

    def _pick_thunderbird_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecione thunderbird.exe",
            "",
            "Executavel (*.exe);;Todos (*.*)",
        )
        if path:
            self.thunderbird_edit.setText(path)

    def _test_nas_path(self) -> None:
        path = normalize_master_path(self.nas_edit.text().strip())
        self.nas_edit.setText(path)
        if not path:
            self._warn("NAS", "Digite um caminho NAS para testar.")
            return
        effective = first_existing_nas_path(path)
        if effective and os.path.exists(effective):
            size_kb = os.path.getsize(effective) / 1024 if os.path.isfile(effective) else 0.0
            msg = f"Caminho acessivel: {effective}"
            if effective != path:
                msg += f"\nFallback aplicado a partir de: {path}"
            if size_kb:
                msg += f"\nTamanho: {size_kb:.1f} KB"
            self._info("NAS", msg)
            return
        tried = " | ".join(nas_fallback_candidates(path) or [path])
        self._error("NAS", f"Não foi possível acessar:\n{tried}")

    def _refresh_data_diagnostics(self) -> None:
        state = self.app_context.state
        cfg = state.config

        sync_status = "OK" if state.startup_sync_ok else "AVISO"
        sync_msg = str(state.startup_sync_message or "-")
        self.lbl_diag_sync.setText(f"Sync master: {sync_status} | {sync_msg}")

        boot_status = "OK" if state.startup_bootstrap_ok else "AVISO"
        boot_msg = str(state.startup_bootstrap_message or "-")
        self.lbl_diag_bootstrap.setText(f"Bootstrap local: {boot_status} | {boot_msg}")

        cache_file = get_cache_file_path()
        cache_ok = cache_file.exists()
        self.lbl_diag_cache.setText(f"Cache XLSX: {'OK' if cache_ok else 'AUSENTE'} | {cache_file}")

        nas_path = normalize_master_path(str(cfg.nas_master_path or "").strip())
        effective = first_existing_nas_path(nas_path) if nas_path else ""
        nas_ok = bool(effective) and os.path.exists(effective)
        shown = effective or nas_path or "-"
        self.lbl_diag_nas.setText(f"NAS XLSX: {'OK' if nas_ok else 'INDISPONIVEL'} | {shown}")

    def _repair_data_bootstrap(self) -> None:
        cfg = self.app_context.state.config
        result = ensure_runtime_bootstrap(cfg, force_refresh=True)
        self.app_context.state.startup_bootstrap_ok = bool(result.ok)
        self.app_context.state.startup_bootstrap_message = str(result.message or "")
        self.app_context.state.startup_bootstrap_warnings = list(result.warnings or [])
        self._refresh_data_diagnostics()
        if result.ok:
            self._info("Saúde da base", "Reparo concluido. Agora clique em 'Atualizar base' em Fornecedores.")
            self._set_status("Reparo da base concluido.")
            return
        extra = "\n".join(result.warnings[:3]) if result.warnings else "Sem detalhes adicionais."
        self._warn("Saúde da base", f"Reparo executado com alerta.\n{result.message}\n{extra}")
        self._set_status("Reparo com alerta. Verifique conectividade com o NAS.")

    def _clear_brave_key(self) -> None:
        if hasattr(self, "brave_key_edit"):
            if hasattr(self, "brave_key_edit"):
                self.brave_key_edit.clear()
        self._clear_brave_key_requested = True
        if hasattr(self, "brave_mask_label"):
            self.brave_mask_label.setText("Atual: (sera limpa ao salvar)")

    def _open_smtp_dialog(self) -> None:
        dialog = SMTPConfigDialog(self, self.app_context.state)
        dialog.exec()

    def _open_signature_dialog(self) -> None:
        from .signature_dialog import SignatureConfigDialog
        dialog = SignatureConfigDialog(self, self.app_context.state)
        if dialog.exec():
            self._load_from_state()
            save_to_master(self.app_context.state.config)
            self._set_status("Assinaturas atualizadas e sincronizadas.")


    def _test_imap_now(self) -> None:
        # Apply fields temporarily before testing so the user can test before saving.
        cfg = self.app_context.state.config
        cfg.ensure_imap_profiles()
        if hasattr(self, "imap_widgets"):
            for key, widgets in self.imap_widgets.items():
                profile = cfg.imap_profiles.get(key)
                if profile is None:
                    continue
                enabled = widgets.get("enabled")
                user = widgets.get("user")
                password = widgets.get("password")
                if isinstance(enabled, QCheckBox):
                    profile.enabled = bool(enabled.isChecked())
                if isinstance(user, QLineEdit):
                    profile.username = user.text().strip()
                if isinstance(password, QLineEdit) and password.text().strip():
                    set_imap_password(profile, password.text().strip())
        summary = sync_inbox_replies(cfg, self.app_context.state.history)
        if summary.errors:
            self._warn("Respostas recebidas", summary.message())
        else:
            self._info("Respostas recebidas", summary.message())
        self._set_status(summary.message())
        self._load_from_state()

    def _export_history_now(self) -> None:
        history = self.app_context.state.history
        if history is None:
            self._warn("Histórico", "Histórico indisponível.")
            return
        ok, message, path = history.export_clean_history_xlsx()
        if ok:
            self._info("Histórico", f"Arquivo atualizado com sucesso:\n{path}")
            self._set_status(f"Histórico exportado: {path}")
            return
        if path:
            self._warn("Histórico", f"Export concluído com alerta:\n{message}\n\nArquivo:\n{path}")
            self._set_status(f"Histórico exportado com alerta: {path}")
            return
        self._error("Histórico", f"Falha ao exportar histórico:\n{message}")
        self._set_status("Falha ao exportar histórico.")

    def _clear_history_with_archive(self) -> None:
        history = self.app_context.state.history
        if history is None:
            self._warn("Histórico", "Histórico indisponível.")
            return

        confirm1 = QMessageBox.question(
            self,
            "Limpar histórico",
            "Isso vai arquivar e limpar o histórico ativo. Deseja continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm1 != QMessageBox.StandardButton.Yes:
            return

        confirm2 = QMessageBox.question(
            self,
            "Confirmação final",
            "Confirmar limpeza agora? Essa ação não pode ser desfeita no arquivo ativo.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm2 != QMessageBox.StandardButton.Yes:
            return

        actor = os.environ.get("USERNAME", "user")
        ok, message, meta = history.clear_history_with_archive(reason="manual_settings_action", actor=actor)
        if not ok:
            self._error("Histórico", f"Falha ao limpar histórico:\n{message}")
            self._set_status("Falha ao limpar histórico.")
            return

        archive_local = str(meta.get("archive_local_dir") or "")
        archive_nas = str(meta.get("archive_nas_dir") or "")
        xlsx_path = str(meta.get("xlsx_path") or "")
        details = [f"Resultado: {message}"]
        if archive_local:
            details.append(f"Arquivo local: {archive_local}")
        if archive_nas:
            details.append(f"Arquivo NAS: {archive_nas}")
        if xlsx_path:
            details.append(f"Histórico XLSX: {xlsx_path}")
        self._info("Histórico", "\n".join(details))
        self._set_status("Histórico arquivado e limpo com sucesso.")

    def _collect_xlsx_sources(self) -> list[str]:
        values: list[str] = []
        for idx in range(self.xlsx_list.count()):
            item = self.xlsx_list.item(idx)
            if item is None:
                continue
            text = item.text().strip()
            if text:
                values.append(text)
        return values

    def _save(self) -> None:
        cfg = self.app_context.state.config
        cfg.xlsx_sources = self._collect_xlsx_sources()
        cfg.nas_master_path = normalize_master_path(self.nas_edit.text().strip())
        cfg.export_history_default_dir = self.export_edit.text().strip()
        cfg.thunderbird_path = self.thunderbird_edit.text().strip()
        cfg.default_subject_prefix = self.subject_prefix_edit.text().strip() or "Cotação"
        cfg.xlsx_sheet_name = self.sheet_name_edit.text().strip() or "Fornecedores"
        cfg.web_search.set_primary_provider(self._web_provider_value())
        cfg.pc_hidden_bcc_enabled = bool(self.chk_hidden_bcc.isChecked())
        if hasattr(self, "chk_imap_auto"):
            cfg.imap_check_on_open_history = bool(self.chk_imap_auto.isChecked())
        if hasattr(self, "imap_widgets"):
            cfg.ensure_imap_profiles()
            for key, widgets in self.imap_widgets.items():
                profile = cfg.imap_profiles.get(key)
                if profile is None:
                    continue
                enabled = widgets.get("enabled")
                user = widgets.get("user")
                password = widgets.get("password")
                if isinstance(enabled, QCheckBox):
                    profile.enabled = bool(enabled.isChecked())
                if isinstance(user, QLineEdit):
                    profile.username = user.text().strip()
                profile.host = "imap.example.com"
                profile.port = 993
                profile.security = "ssl"
                profile.mailbox = "INBOX"
                if isinstance(password, QLineEdit) and password.text().strip():
                    set_imap_password(profile, password.text().strip())

        brave_key = self.brave_key_edit.text().strip() if hasattr(self, "brave_key_edit") else ""
        cfg.web_search.set_primary_provider("disabled")
        cfg.web_search.enable_duckduckgo_search_fallback = False
        cfg.web_search.enable_heavy_fallback = False
        if self._clear_brave_key_requested:
            cfg.web_search.clear_brave_api_key()
        elif brave_key:
            cfg.web_search.set_brave_api_key(brave_key)

        cfg.save()
        self._clear_brave_key_requested = False
        if hasattr(self, "brave_key_edit"):
            self.brave_key_edit.clear()

        ok, message = save_to_master(cfg)
        if ok:
            self._info("Configurações", "Configurações salvas localmente e no servidor.")
        else:
            self._warn("Configurações", f"Salvas localmente.\n\nSync servidor: {message}")
        self._set_status(message if message else "Configurações salvas.")
        self._load_from_state()

    def _export_config(self) -> None:
        default_name = get_default_export_filename()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar configuracao",
            str(Path.home() / default_name),
            "JSON (*.json);;Todos (*.*)",
        )
        if not path:
            return
        try:
            export_config(path)
            self._info("Exportar", f"Configuracao exportada para:\n{path}")
            self._set_status(f"Configuracao exportada: {path}")
        except Exception as exc:
            self._error("Exportar", f"Falha ao exportar configuracao: {exc}")

    def _import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar configuracao",
            "",
            "JSON (*.json);;Todos (*.*)",
        )
        if not path:
            return
        ok, message = import_config(path)
        if not ok:
            self._error("Importar", message)
            return
        self.app_context.state.config = AppConfig.load()
        self._load_from_state()
        self._info("Importar", message)
        self._set_status("Configuracao importada com sucesso.")

    def _set_status(self, text: str) -> None:
        if self._on_status is not None:
            self._on_status(text)

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _error(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)
