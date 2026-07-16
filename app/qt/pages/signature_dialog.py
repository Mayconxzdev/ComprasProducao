from __future__ import annotations

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QInputDialog, QMessageBox, QFrame,
    QSplitter, QWidget, QFileDialog
)
from PySide6.QtCore import Qt

from app.core.state import AppState
from app.qt.ui_scale import font_css
from app.core.utils_text import normalize_text


class SignatureConfigDialog(QDialog):
    def __init__(self, parent: QWidget | None, state: AppState) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Gerenciar Assinaturas de E-mail")
        self.resize(700, 450)
        self.setModal(True)

        # Copia da config em memoria
        self.signatures = {}
        if not getattr(self.state.config, "email_signatures", None):
            self.state.config.email_signatures = {}

        from app.core.email_signature import signature_paths_for_config

        self.signatures = signature_paths_for_config(self.state.config)

        self.current_user = ""
        self._build_ui()
        self._load_users()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)

        # Esquerda: Lista de Usuarios
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        label_users = QLabel("Usuários:")
        label_users.setStyleSheet(font_css(12, 600))
        left_layout.addWidget(label_users)

        self.user_list = QListWidget()
        self.user_list.currentItemChanged.connect(self._on_user_selected)
        left_layout.addWidget(self.user_list, 1)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Adicionar")
        self.btn_add.clicked.connect(self._add_user)
        self.btn_remove = QPushButton("Remover")
        self.btn_remove.clicked.connect(self._remove_user)

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_remove)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_widget)

        # Direita: Caminhos das Assinaturas
        right_widget = QFrame()
        right_widget.setFrameShape(QFrame.Shape.StyledPanel)
        right_layout = QVBoxLayout(right_widget)

        label_details = QLabel("Detalhes da Assinatura (Caminhos HTML)")
        label_details.setStyleSheet(font_css(14, 600))
        right_layout.addWidget(label_details)

        self.edit_vesper = QLineEdit()
        self.edit_vesper.textChanged.connect(lambda t: self._update_path("vesper", t))
        right_layout.addWidget(QLabel("Perfil Vesper:"))
        right_layout.addLayout(self._create_path_row(self.edit_vesper))

        self.edit_ventrio = QLineEdit()
        self.edit_ventrio.textChanged.connect(lambda t: self._update_path("ventrio", t))
        right_layout.addWidget(QLabel("Perfil VentRio:"))
        right_layout.addLayout(self._create_path_row(self.edit_ventrio))

        self.edit_producao = QLineEdit()
        self.edit_producao.textChanged.connect(lambda t: self._update_path("producao", t))
        right_layout.addWidget(QLabel("Perfil Produção (Fallback VentRio):"))
        right_layout.addLayout(self._create_path_row(self.edit_producao))

        right_layout.addStretch(1)
        splitter.addWidget(right_widget)
        splitter.setSizes([200, 500])

        # Botões da Base
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        bottom_layout.addWidget(btn_cancel)

        btn_save = QPushButton("Salvar Alterações")
        btn_save.setObjectName("accent")
        btn_save.clicked.connect(self._save_and_close)
        bottom_layout.addWidget(btn_save)

        main_layout.addLayout(bottom_layout)

        self.right_widget = right_widget
        self._update_right_panel_state()

    def _create_path_row(self, line_edit: QLineEdit) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.addWidget(line_edit, 1)
        btn = QPushButton("Procurar")
        btn.clicked.connect(lambda: self._browse_path(line_edit))
        layout.addWidget(btn)
        return layout

    def _load_users(self) -> None:
        self.user_list.clear()
        for user in sorted(self.signatures.keys()):
            self.user_list.addItem(QListWidgetItem(user))

    def _update_right_panel_state(self) -> None:
        has_sel = bool(self.current_user)
        self.right_widget.setEnabled(has_sel)
        if not has_sel:
            self.edit_vesper.blockSignals(True)
            self.edit_ventrio.blockSignals(True)
            self.edit_producao.blockSignals(True)

            self.edit_vesper.clear()
            self.edit_ventrio.clear()
            self.edit_producao.clear()

            self.edit_vesper.blockSignals(False)
            self.edit_ventrio.blockSignals(False)
            self.edit_producao.blockSignals(False)

    def _on_user_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self.current_user = ""
            self._update_right_panel_state()
            return

        self.current_user = current.text()
        self._update_right_panel_state()

        cfg = self.signatures.get(self.current_user, {})

        self.edit_vesper.blockSignals(True)
        self.edit_ventrio.blockSignals(True)
        self.edit_producao.blockSignals(True)

        self.edit_vesper.setText(cfg.get("vesper", ""))
        self.edit_ventrio.setText(cfg.get("ventrio", ""))
        self.edit_producao.setText(cfg.get("producao", ""))

        self.edit_vesper.blockSignals(False)
        self.edit_ventrio.blockSignals(False)
        self.edit_producao.blockSignals(False)

    def _update_path(self, profile: str, text: str) -> None:
        if not self.current_user:
            return
        if self.current_user not in self.signatures:
            self.signatures[self.current_user] = {}
        self.signatures[self.current_user][profile] = text

    def _browse_path(self, line_edit: QLineEdit) -> None:
        start_dir = line_edit.text().strip()
        if not start_dir or not os.path.exists(start_dir):
            start_dir = os.path.expanduser("~")

        if not os.path.exists(start_dir):
            start_dir = os.path.expanduser("~")

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecione o arquivo de assinatura (HTML)",
            start_dir,
            "Arquivos HTML (*.html *.htm);;Todos (*.*)"
        )
        if path:
            path = os.path.normpath(path)
            line_edit.setText(path)

    def _add_user(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Novo Usuário", "Nome do usuário (como loga no app/windows):"
        )
        if ok and name.strip():
            user_norm = normalize_text(name.strip())
            if user_norm in self.signatures:
                QMessageBox.warning(self, "Aviso", "Este usuário já existe na lista.")
                return
            self.signatures[user_norm] = {"vesper": "", "ventrio": "", "producao": ""}
            item = QListWidgetItem(user_norm)
            self.user_list.addItem(item)
            self.user_list.setCurrentItem(item)

    def _remove_user(self) -> None:
        item = self.user_list.currentItem()
        if not item:
            return
        user = item.text()
        ans = QMessageBox.question(
            self, "Confirmação", f"Remover assinaturas de '{user}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans == QMessageBox.StandardButton.Yes:
            if user in self.signatures:
                del self.signatures[user]
            row = self.user_list.row(item)
            self.user_list.takeItem(row)
            if self.user_list.count() > 0:
                self.user_list.setCurrentRow(min(row, self.user_list.count() - 1))
            else:
                self.current_user = ""
                self._update_right_panel_state()

    def _save_and_close(self) -> None:
        cfg = self.state.config

        # Limpar dicionario e repopular, removemos vazios
        clean_sigs = {}
        for user, profiles in self.signatures.items():
            vp = profiles.get("vesper", "").strip()
            vt = profiles.get("ventrio", "").strip()
            pr = profiles.get("producao", "").strip()

            # auto-preencher producao se ventrio estiver preenchido e producao nao
            if vt and not pr:
                pr = vt

            clean_sigs[user] = {
                "vesper": vp,
                "ventrio": vt,
                "producao": pr
            }

        cfg.email_signatures = clean_sigs
        cfg.email_signatures_managed = True
        cfg.save()
        self.accept()
