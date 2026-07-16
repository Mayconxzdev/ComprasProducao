from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.application.context import AppContext
from app.core.base_health import analyze_base
from app.core.supplier_meta_store_nas import SupplierMetaStoreNAS

from app.qt.services import SupplierEditService


def _clean(value) -> str:
    return str(value or "").strip()


class AdminAuditDialog(QDialog):
    overridesChanged = Signal()

    def __init__(
        self,
        *,
        app_context: AppContext,
        edit_service: SupplierEditService,
        on_status: Callable[[str], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.app_context = app_context
        self.edit_service = edit_service
        self._on_status = on_status
        self._duplicates: list[tuple[object, object, str]] = []

        self.setWindowTitle("Admin | Auditoria da Base")
        self.resize(980, 640)
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("Modo Admin (oculto)")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)

        subtitle = QLabel("Auditoria de campos obrigatorios e duplicados suspeitos.")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)

        summary = QFrame(self)
        summary.setObjectName("pageCard")
        summary_box = QHBoxLayout(summary)
        summary_box.setContentsMargins(10, 10, 10, 10)
        self.lbl_missing = QLabel("-")
        self.lbl_duplicates = QLabel("-")
        self.lbl_items_without = QLabel("-")
        summary_box.addWidget(self.lbl_missing)
        summary_box.addWidget(self.lbl_duplicates)
        summary_box.addWidget(self.lbl_items_without)
        summary_box.addStretch(1)
        root.addWidget(summary)

        self.table_duplicates = QTableWidget(0, 3, self)
        self.table_duplicates.setHorizontalHeaderLabels(["Fornecedor A", "Fornecedor B", "Dominio"])
        self.table_duplicates.verticalHeader().setVisible(False)
        self.table_duplicates.setAlternatingRowColors(True)
        self.table_duplicates.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table_duplicates, 1)

        actions = QHBoxLayout()
        self.btn_merge = QPushButton("Merge: usar nome do A no B")
        self.btn_merge.clicked.connect(self._merge_selected_duplicate)
        actions.addWidget(self.btn_merge)
        actions.addStretch(1)

        btn_refresh = QPushButton("Atualizar auditoria")
        btn_refresh.clicked.connect(self._refresh)
        actions.addWidget(btn_refresh)

        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.accept)
        actions.addWidget(btn_close)
        root.addLayout(actions)

    def _refresh(self) -> None:
        try:
            meta = self.app_context.state.supplier_meta or SupplierMetaStoreNAS(self.app_context.state.config)
            report = analyze_base(self.app_context.state.index, meta)
        except Exception as exc:
            QMessageBox.critical(self, "Auditoria", f"Falha ao analisar base: {exc}")
            return

        self._duplicates = list(report.possible_duplicates or [])
        self.lbl_missing.setText(f"Sem e-mail: {len(report.missing_email)}")
        self.lbl_duplicates.setText(f"Duplicados suspeitos: {len(self._duplicates)}")
        self.lbl_items_without.setText(f"Itens sem fornecedor: {len(report.items_without_supplier)}")
        self._render_duplicates_table()
        self._set_status("Auditoria da base atualizada.")

    def _render_duplicates_table(self) -> None:
        self.table_duplicates.setRowCount(0)
        for row_idx, (left, right, domain) in enumerate(self._duplicates):
            self.table_duplicates.insertRow(row_idx)
            left_text = f"{_clean(getattr(left, 'empresa', ''))} | {_clean(getattr(left, 'email', ''))}"
            right_text = f"{_clean(getattr(right, 'empresa', ''))} | {_clean(getattr(right, 'email', ''))}"
            self.table_duplicates.setItem(row_idx, 0, QTableWidgetItem(left_text))
            self.table_duplicates.setItem(row_idx, 1, QTableWidgetItem(right_text))
            self.table_duplicates.setItem(row_idx, 2, QTableWidgetItem(_clean(domain)))

    def _merge_selected_duplicate(self) -> None:
        row = self.table_duplicates.currentRow()
        if row < 0 or row >= len(self._duplicates):
            QMessageBox.warning(self, "Merge", "Selecione um duplicado primeiro.")
            return
        left, right, _domain = self._duplicates[row]
        company = _clean(getattr(left, "empresa", ""))
        if not company:
            QMessageBox.warning(self, "Merge", "Fornecedor A sem nome valido.")
            return

        ok, message = self.edit_service.set_company_override_for_supplier(right, company)
        if not ok:
            QMessageBox.critical(self, "Merge", message)
            return

        self.overridesChanged.emit()
        self._set_status("Merge aplicado no modo admin.")
        QMessageBox.information(self, "Merge", "Nome aplicado em fornecedor B com override local.")

    def _set_status(self, text: str) -> None:
        if self._on_status is not None:
            self._on_status(text)
