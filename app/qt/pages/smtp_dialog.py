from __future__ import annotations

import base64
import re

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.config import SMTPProfile
from app.core.config_sync import save_to_master
from app.core.dpapi_crypto import encrypt_password, is_available as dpapi_available
from app.core.smtp_handler import test_smtp_profile
from app.core.state import AppState
from app.core.utils_text import normalize_text
from app.qt.widgets.smart_suggest_line_edit import (
    SmartSuggestLineEdit,
    SuggestionOption,
    resolve_suggestions,
)
from app.qt.ui_scale import font_css, scaled_px, scaled_window_size


_PROFILE_KEY_RE = re.compile(r"[^a-z0-9_]+")


def smtp_security_options() -> list[SuggestionOption]:
    return [
        SuggestionOption(label="SSL/TLS (465)", value="SSL/TLS (465)", payload="ssl"),
        SuggestionOption(label="STARTTLS (587)", value="STARTTLS (587)", payload="starttls"),
    ]


class _SMTPTestSignals(QObject):
    done = Signal(bool, str)


class _SMTPTestRunnable(QRunnable):
    def __init__(self, state: AppState, signals: _SMTPTestSignals):
        super().__init__()
        self._state = state
        self._signals = signals

    @Slot()
    def run(self) -> None:
        ok, msg = test_smtp_profile(self._state.config)
        try:
            self._signals.done.emit(bool(ok), str(msg))
        except RuntimeError:
            pass


class SMTPConfigDialog(QDialog):
    def __init__(self, parent: QWidget, app_state: AppState):
        super().__init__(parent)
        self.app_state = app_state
        self._thread_pool = QThreadPool.globalInstance()
        self._test_signals = _SMTPTestSignals(self)
        self._test_signals.done.connect(self._on_test_done)

        self.setWindowTitle("Configuracao SMTP")
        self.setModal(True)
        self.resize(*scaled_window_size(820, 620, min_width=700, min_height=520))
        self.setMinimumSize(scaled_px(700), scaled_px(520))

        self._build_ui()
        self._load_current_profile()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Servidor de Envio (SMTP)")
        title.setStyleSheet(font_css(18, 700))
        root.addWidget(title)

        desc = QLabel("Configure os perfis SMTP. Senhas sao protegidas localmente com DPAPI.")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        root.addWidget(desc)

        form_wrap = QFrame(self)
        form_wrap.setObjectName("pageCard")
        root.addWidget(form_wrap, 1)
        form = QFormLayout(form_wrap)
        form.setLabelAlignment(form.labelAlignment() | form.labelAlignment())
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.profile_field = SmartSuggestLineEdit(form_wrap, allow_manual=False, debounce_ms=150)
        self.profile_field.set_provider(self._provider_profile)
        self.profile_field.committed.connect(self._on_profile_committed)
        self.profile_field.changed.connect(self._on_profile_changed)
        self.profile_field.setMinimumWidth(scaled_px(300))

        profile_row = QWidget(form_wrap)
        profile_layout = QVBoxLayout(profile_row)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(8)

        profile_field_row = QHBoxLayout()
        profile_field_row.setContentsMargins(0, 0, 0, 0)
        profile_field_row.setSpacing(8)
        profile_field_row.addWidget(self.profile_field, 1)
        profile_layout.addLayout(profile_field_row)

        profile_actions_row = QHBoxLayout()
        profile_actions_row.setContentsMargins(0, 0, 0, 0)
        profile_actions_row.setSpacing(8)
        self.btn_new_profile = QPushButton("Novo perfil")
        self.btn_new_profile.clicked.connect(self._create_profile)
        profile_actions_row.addWidget(self.btn_new_profile)
        self.btn_rename_profile = QPushButton("Renomear perfil")
        self.btn_rename_profile.clicked.connect(self._rename_current_profile)
        profile_actions_row.addWidget(self.btn_rename_profile)
        self.btn_delete_profile = QPushButton("Excluir perfil")
        self.btn_delete_profile.clicked.connect(self._delete_current_profile)
        profile_actions_row.addWidget(self.btn_delete_profile)
        profile_actions_row.addStretch(1)
        profile_layout.addLayout(profile_actions_row)

        form.addRow("Perfil SMTP:", profile_row)

        self.host_edit = QLineEdit(form_wrap)
        form.addRow("Servidor SMTP:", self.host_edit)

        port_security = QWidget(form_wrap)
        ps_layout = QGridLayout(port_security)
        ps_layout.setContentsMargins(0, 0, 0, 0)
        ps_layout.setHorizontalSpacing(10)

        self.port_edit = QLineEdit(port_security)
        self.port_edit.setMaximumWidth(scaled_px(120))
        ps_layout.addWidget(self.port_edit, 0, 0)

        self.security_field = SmartSuggestLineEdit(port_security, allow_manual=False, debounce_ms=150)
        self.security_field.set_provider(self._provider_security)
        self.security_field.committed.connect(self._on_security_committed)
        ps_layout.addWidget(self.security_field, 0, 1)
        form.addRow("Porta / Seguranca:", port_security)

        self.username_edit = QLineEdit(form_wrap)
        self.username_edit.textChanged.connect(self._sync_from_bcc_fields)
        form.addRow("Usuario:", self.username_edit)

        self.from_edit = QLineEdit(form_wrap)
        self.from_edit.setReadOnly(True)
        form.addRow("Enviando como:", self.from_edit)

        self.bcc_edit = QLineEdit(form_wrap)
        self.bcc_edit.setReadOnly(True)
        form.addRow("BCC automatico:", self.bcc_edit)

        self.password_status = QLabel("Senha não configurada")
        self.password_status.setObjectName("muted")
        form.addRow("Status da senha:", self.password_status)

        self.btn_set_password = QPushButton("Definir / Atualizar senha")
        self.btn_set_password.clicked.connect(self._set_password)
        form.addRow("", self.btn_set_password)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_test = QPushButton("Testar SMTP")
        self.btn_test.clicked.connect(self._test_connection)
        buttons.addWidget(self.btn_test)

        self.btn_save = QPushButton("Salvar")
        self.btn_save.setObjectName("accent")
        self.btn_save.clicked.connect(self._save)
        buttons.addWidget(self.btn_save)

        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.reject)
        buttons.addWidget(btn_close)
        root.addLayout(buttons)

    def _provider_profile(self, query: str, force: bool) -> list[SuggestionOption]:
        return resolve_suggestions(self._profile_options(), query, force=force, limit=12)

    def _provider_security(self, query: str, force: bool) -> list[SuggestionOption]:
        return resolve_suggestions(smtp_security_options(), query, force=force, limit=12)

    def _profile_key_from_text(self, text: str) -> str:
        normalized = normalize_text(text or "")
        for key, profile in self.app_state.config.smtp_profiles.items():
            candidates = {
                normalize_text(key),
                normalize_text(self._profile_label_from_key(key)),
                normalize_text(profile.label or ""),
            }
            if normalized in candidates:
                return key
            # Keep legacy behavior for built-in aliases
            if key == "producao" and normalized.startswith("producao"):
                return key
            if key == "teste" and normalized.startswith("teste"):
                return key
        if self.app_state.config.smtp_active_profile in self.app_state.config.smtp_profiles:
            return self.app_state.config.smtp_active_profile
        if self.app_state.config.smtp_profiles:
            return next(iter(self.app_state.config.smtp_profiles.keys()))
        return "producao"

    def _default_profile_label(self, key: str) -> str:
        if key == "producao":
            return "Producao (compras)"
        if key == "teste":
            return "Teste local"
        return key

    def _profile_label_from_key(self, key: str) -> str:
        profile = self.app_state.config.smtp_profiles.get(key)
        if profile is None:
            return self._default_profile_label(key)
        raw_label = str(profile.label or "").strip()
        if raw_label:
            # Keep canonical fallback only when label still matches a default form.
            norm = normalize_text(raw_label)
            if key == "producao" and norm in {"producao (compras)", "producao"}:
                return "Producao (compras)"
            if key == "teste" and norm in {"teste local", "teste"}:
                return "Teste local"
            return raw_label
        return self._default_profile_label(key)

    def _profile_options(self) -> list[SuggestionOption]:
        options: list[SuggestionOption] = []
        for key in self.app_state.config.smtp_profiles.keys():
            label = self._profile_label_from_key(key)
            options.append(SuggestionOption(label=label, value=label, payload=key))
        return options

    def _build_profile_key(self, label: str) -> str:
        raw = normalize_text(label)
        base = _PROFILE_KEY_RE.sub("_", raw).strip("_")
        if not base:
            return ""
        if base[0].isdigit():
            base = f"perfil_{base}"
        key = base
        suffix = 2
        while key in self.app_state.config.smtp_profiles:
            key = f"{base}_{suffix}"
            suffix += 1
        return key

    def _load_profile(self, key: str, *, sync_profile_field: bool) -> None:
        profile_key = key if key in self.app_state.config.smtp_profiles else self._profile_key_from_text(key)
        profile = self.app_state.config.smtp_profiles.get(profile_key)
        if profile is None:
            return

        if sync_profile_field:
            self.profile_field.set_value(self._profile_label_from_key(profile_key))

        self.host_edit.setText(profile.host or "")
        self.port_edit.setText(str(profile.port or 465))
        self.username_edit.setText(profile.username or "")
        # Keep "from" and "bcc" always aligned with username for this app.
        self.from_edit.setText(profile.username or "")
        self.bcc_edit.setText(profile.username or "")
        if (profile.security or "").lower() == "starttls":
            self.security_field.set_value("STARTTLS (587)")
        else:
            self.security_field.set_value("SSL/TLS (465)")
        self._refresh_password_status(profile)

    def _current_profile(self) -> SMTPProfile | None:
        key = self._profile_key_from_text(self.profile_field.value())
        return self.app_state.config.smtp_profiles.get(key)

    def _sync_from_bcc_fields(self) -> None:
        text = self.username_edit.text().strip()
        self.from_edit.setText(text)
        self.bcc_edit.setText(text)

    def _on_profile_changed(self, _text: str) -> None:
        # Keep typing fluid. Live-reload only for exact known values.
        text_norm = normalize_text(self.profile_field.value())
        canonical = {
            normalize_text(key)
            for key in self.app_state.config.smtp_profiles.keys()
        } | {
            normalize_text(self._profile_label_from_key(key))
            for key in self.app_state.config.smtp_profiles.keys()
        } | {
            normalize_text((profile.label or ""))
            for profile in self.app_state.config.smtp_profiles.values()
        }
        if text_norm not in canonical:
            return
        key = self._profile_key_from_text(self.profile_field.value())
        self._load_profile(key, sync_profile_field=False)

    def _on_profile_committed(self, text: str, _from_catalog: bool, _payload) -> None:
        if not text.strip():
            return
        key = self._profile_key_from_text(text)
        self.app_state.config.smtp_active_profile = key
        self._load_profile(key, sync_profile_field=True)

    def _on_security_committed(self, text: str, _from_catalog: bool, _payload) -> None:
        if "465" in text:
            self.port_edit.setText("465")
        elif "587" in text:
            self.port_edit.setText("587")

    def _create_profile(self) -> None:
        label, ok = QInputDialog.getText(
            self,
            "Novo perfil SMTP",
            "Nome do novo perfil:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        label = str(label or "").strip()
        if not label:
            self._warn("Perfil", "Informe um nome para o perfil.")
            return
        key = self._build_profile_key(label)
        if not key:
            self._warn("Perfil", "Nome invalido para perfil.")
            return

        # New profiles always inherit transport from "teste" profile baseline.
        base = self.app_state.config.smtp_profiles.get("teste") or SMTPProfile()
        self.app_state.config.smtp_profiles[key] = SMTPProfile(
            label=label,
            host=base.host,
            port=base.port,
            security=base.security,
            auth_method=base.auth_method,
            username="",
            from_email="",
            bcc_email="",
            timeout_sec=base.timeout_sec,
            password_protected_b64="",
            shared_password_b64="",
        )
        self.app_state.config.smtp_active_profile = key
        self._load_profile(key, sync_profile_field=True)
        self._persist_config_with_master_sync("SMTP", f"Perfil '{label}' criado.")

    def _rename_current_profile(self) -> None:
        key = self._profile_key_from_text(self.profile_field.value())
        profile = self.app_state.config.smtp_profiles.get(key)
        if profile is None:
            self._warn("Perfil", "Selecione um perfil valido para renomear.")
            return
        current_label = self._profile_label_from_key(key)
        new_label, ok = QInputDialog.getText(
            self,
            "Renomear perfil SMTP",
            "Novo nome do perfil:",
            QLineEdit.EchoMode.Normal,
            current_label,
        )
        if not ok:
            return
        new_label = str(new_label or "").strip()
        if not new_label:
            self._warn("Perfil", "Informe um nome valido para o perfil.")
            return

        # Prevent ambiguous labels in selector.
        target_norm = normalize_text(new_label)
        for other_key in self.app_state.config.smtp_profiles.keys():
            if other_key == key:
                continue
            if normalize_text(self._profile_label_from_key(other_key)) == target_norm:
                self._warn("Perfil", "Ja existe um perfil com esse nome.")
                return

        profile.label = new_label
        self.profile_field.set_value(new_label)
        self._persist_config_with_master_sync("SMTP", "Nome do perfil atualizado.")

    def _delete_current_profile(self) -> None:
        key = self._profile_key_from_text(self.profile_field.value())
        profile = self.app_state.config.smtp_profiles.get(key)
        if profile is None:
            self._warn("Perfil", "Selecione um perfil valido para excluir.")
            return
        if len(self.app_state.config.smtp_profiles) <= 1:
            self._warn("Perfil", "Não é possível excluir o último perfil SMTP.")
            return
        resp = QMessageBox.question(
            self,
            "Excluir perfil",
            f"Excluir o perfil '{self._profile_label_from_key(key)}'?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        del self.app_state.config.smtp_profiles[key]
        if self.app_state.config.smtp_active_profile == key:
            self.app_state.config.smtp_active_profile = next(iter(self.app_state.config.smtp_profiles.keys()))
        self._load_current_profile()
        self._persist_config_with_master_sync("SMTP", "Perfil excluido.")

    def _load_current_profile(self) -> None:
        key = self.app_state.config.smtp_active_profile
        self._load_profile(key, sync_profile_field=True)

    def _refresh_password_status(self, profile: SMTPProfile) -> None:
        if profile.password_protected_b64 or profile.shared_password_b64:
            self.password_status.setText("Senha configurada")
            self.password_status.setStyleSheet("color: #10b981;")
        else:
            self.password_status.setText("Senha não configurada")
            self.password_status.setStyleSheet("color: #f59e0b;")

    def _set_password(self) -> None:
        profile = self._current_profile()
        if profile is None:
            self._warn("Perfil invalido", "Selecione um perfil SMTP valido.")
            return
        if not dpapi_available():
            self._error("DPAPI indisponível", "Não foi possível usar criptografia local (DPAPI).")
            return

        password, ok = QInputDialog.getText(
            self,
            "Senha SMTP",
            f"Digite a senha para {profile.username or 'perfil selecionado'}:",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        password = (password or "").strip()
        if not password:
            return

        try:
            profile.password_protected_b64 = encrypt_password(password)
            profile.shared_password_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
            self._refresh_password_status(profile)
            self._persist_config_with_master_sync("Senha", "Senha atualizada com sucesso.")
        except Exception as exc:
            self._error("Senha", f"Falha ao proteger senha: {exc}")

    def _validate_fields(self) -> bool:
        if not self.host_edit.text().strip():
            self._warn("Validacao", "Preencha o servidor SMTP.")
            return False
        if not self.port_edit.text().strip().isdigit():
            self._warn("Validacao", "Porta SMTP invalida.")
            return False
        if not self.username_edit.text().strip():
            self._warn("Validacao", "Preencha o usuario SMTP.")
            return False
        return True

    def _apply_form_to_profile(self) -> bool:
        if not self._validate_fields():
            return False
        profile = self._current_profile()
        if profile is None:
            self._warn("Perfil", "Selecione um perfil SMTP valido.")
            return False

        profile.host = self.host_edit.text().strip()
        profile.port = int(self.port_edit.text().strip())
        profile.security = "ssl" if "465" in self.security_field.value() else "starttls"
        profile.username = self.username_edit.text().strip()
        profile.from_email = profile.username
        profile.bcc_email = profile.username
        # Keep transport unified for all profiles (only username/password vary by profile).
        self.app_state.config.apply_smtp_transport_to_all_profiles(
            profile.host,
            profile.port,
            profile.security,
        )

        self.app_state.config.smtp_active_profile = self._profile_key_from_text(self.profile_field.value())
        return True

    def _save(self) -> None:
        if not self._apply_form_to_profile():
            return
        self._persist_config_with_master_sync("SMTP", "Configuracao SMTP salva.")

    def _test_connection(self) -> None:
        if not self._apply_form_to_profile():
            return
        self.app_state.config.save()
        self._set_testing(True)
        self._thread_pool.start(_SMTPTestRunnable(self.app_state, self._test_signals))

    def _on_test_done(self, ok: bool, message: str) -> None:
        self._set_testing(False)
        if ok:
            self._info("Teste SMTP", message)
        else:
            self._error("Teste SMTP", message)

    def _set_testing(self, testing: bool) -> None:
        self.btn_test.setEnabled(not testing)
        self.btn_save.setEnabled(not testing)
        self.btn_set_password.setEnabled(not testing)
        self.btn_new_profile.setEnabled(not testing)
        self.btn_rename_profile.setEnabled(not testing)
        self.btn_delete_profile.setEnabled(not testing)
        if testing:
            self.btn_test.setText("Testando...")
        else:
            self.btn_test.setText("Testar SMTP")

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _error(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)

    def _persist_config_with_master_sync(self, title: str, success_message: str) -> None:
        self.app_state.config.save()
        ok, message = save_to_master(self.app_state.config)
        if ok:
            self._info(title, success_message)
            return
        self._warn(title, f"{success_message}\nSalva localmente. Sync servidor: {message}")
