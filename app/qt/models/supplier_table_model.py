from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from enum import IntEnum
from typing import Any, Callable, Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from app.core.utils_text import normalize_text


class SupplierColumns(IntEnum):
    SELECT = 0
    PRODUCTS = 1
    COMPANY = 2
    CONTACT = 3
    EMAIL = 4
    PHONE = 5
    SCORE = 6


@dataclass(frozen=True)
class SupplierRow:
    supplier_key: str
    supplier_id: str
    is_local: bool
    company: str
    contact: str
    phone: str
    email: str
    products: tuple[str, ...]
    operational_score: int = 0
    score_reason: str = ""
    score_breakdown: tuple[tuple[str, int], ...] = ()
    selected: bool = False
    raw_supplier: object | None = None
    meta_key: str = ""
    meta_status: str = "ATIVO"
    meta_notes: str = ""

    @property
    def email_norm(self) -> str:
        return normalize_text(self.email)

    @property
    def products_text(self) -> str:
        if not self.products:
            return ""
        if len(self.products) == 1:
            return self.products[0]
        return f"{self.products[0]} (+{len(self.products) - 1})"

    @property
    def products_edit_text(self) -> str:
        return ", ".join(self.products)

    @cached_property
    def search_blob_norm(self) -> str:
        # Cache lógico da linha: o proxy usa isso para filtrar sem normalizar
        # todos os campos a cada tecla. Como SupplierRow é imutável, o valor
        # calculado fica estável enquanto a linha existir.
        return normalize_text(
            " ".join([self.company, self.contact, self.phone, self.email, self.products_edit_text])
        )


class SupplierTableModel(QAbstractTableModel):
    rowEditFailed = Signal(str)
    rowPersisted = Signal(str)
    selectedEmailsChanged = Signal(object)

    HEADERS = (
        "",
        "Produto",
        "Empresa",
        "Contato",
        "E-mail",
        "Telefone",
        "Score",
    )

    def __init__(
        self,
        *,
        edit_handler: Callable[[SupplierRow, str, str], tuple[bool, SupplierRow, str]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[SupplierRow] = []
        self._edit_handler = edit_handler

    def set_rows(self, rows: Iterable[SupplierRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()
        self.selectedEmailsChanged.emit(self.selected_email_set())

    def rows(self) -> list[SupplierRow]:
        return list(self._rows)

    def row_at(self, row: int) -> SupplierRow | None:
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def selected_email_set(self) -> set[str]:
        emails: set[str] = set()
        for row in self._rows:
            if row.selected and row.email_norm:
                emails.add(row.email_norm)
        return emails

    def set_selected_rows(self, source_rows: Iterable[int], selected: bool) -> bool:
        """Marca/desmarca várias linhas com um único sinal de atualização.

        Usado por "Selecionar resultados"/"Limpar seleção" para evitar centenas
        de chamadas setData(), dataChanged e selectedEmailsChanged seguidas.
        """
        changed_rows: list[int] = []
        for row_idx in sorted(set(int(r) for r in source_rows if 0 <= int(r) < len(self._rows))):
            row = self._rows[row_idx]
            if not row.email_norm or row.selected == selected:
                continue
            self._rows[row_idx] = replace(row, selected=selected)
            changed_rows.append(row_idx)
        if not changed_rows:
            return False
        left = self.index(min(changed_rows), SupplierColumns.SELECT)
        right = self.index(max(changed_rows), SupplierColumns.SCORE)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.DisplayRole])
        self.selectedEmailsChanged.emit(self.selected_email_set())
        return True

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return None
        column = index.column()

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.ForegroundRole:
            if not row.email:
                from PySide6.QtGui import QColor
                return QColor("#888888")
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            if column == SupplierColumns.SELECT and not row.email:
                return "Este fornecedor não possui e-mail cadastrado e não pode ser selecionado."

        if column == SupplierColumns.SELECT:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if row.selected else Qt.CheckState.Unchecked
            if role == Qt.ItemDataRole.DisplayRole:
                return ""
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            return None

        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
            return None

        if column == SupplierColumns.PRODUCTS:
            if role == Qt.ItemDataRole.EditRole:
                return row.products_edit_text
            return row.products_text
        if column == SupplierColumns.COMPANY:
            return row.company
        if column == SupplierColumns.CONTACT:
            return row.contact
        if column == SupplierColumns.EMAIL:
            if role == Qt.ItemDataRole.EditRole:
                return row.email
            if not row.email:
                return "Sem e-mail"
            return row.email
        if column == SupplierColumns.PHONE:
            return row.phone
        if column == SupplierColumns.SCORE:
            if role == Qt.ItemDataRole.EditRole:
                return row.operational_score
            if role == Qt.ItemDataRole.ToolTipRole:
                if row.score_reason:
                    return row.score_reason
                if row.score_breakdown:
                    return ", ".join(f"{key}: {value}" for key, value in row.score_breakdown)
            return row.operational_score
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        row = self.row_at(index.row())
        if row is None:
            return Qt.ItemFlag.ItemIsEnabled

        column = index.column()
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        if column == SupplierColumns.SELECT:
            # A seleção é controlada pela página/delegate para que o clique na
            # linha inteira alterne o estado uma única vez. Deixar
            # ItemIsUserCheckable aqui fazia o Qt alternar a checkbox e depois
            # o handler de clique alternar de novo em alguns PCs/temas.
            return base

        if column in {
            SupplierColumns.COMPANY,
            SupplierColumns.CONTACT,
            SupplierColumns.PHONE,
            SupplierColumns.EMAIL,
            SupplierColumns.PRODUCTS,
        }:
            return base | Qt.ItemFlag.ItemIsEditable

        return base

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if not index.isValid():
            return False

        row = self.row_at(index.row())
        if row is None:
            return False

        column = index.column()

        if column == SupplierColumns.SELECT and role == Qt.ItemDataRole.CheckStateRole:
            if not row.email_norm:
                return False
            if isinstance(value, bool):
                next_selected = bool(value)
            elif value is None:
                next_selected = False
            else:
                try:
                    next_selected = int(value) == int(Qt.CheckState.Checked)
                except (TypeError, ValueError):
                    next_selected = value == Qt.CheckState.Checked
            if row.selected == next_selected:
                return False
            self._rows[index.row()] = replace(row, selected=next_selected)
            left = self.index(index.row(), SupplierColumns.SELECT)
            right = self.index(index.row(), SupplierColumns.SCORE)
            self.dataChanged.emit(
                left,
                right,
                [
                    Qt.ItemDataRole.CheckStateRole,
                    Qt.ItemDataRole.DisplayRole,
                ],
            )
            self.selectedEmailsChanged.emit(self.selected_email_set())
            return True

        if role != Qt.ItemDataRole.EditRole:
            return False

        field_map = {
            SupplierColumns.COMPANY: "company",
            SupplierColumns.CONTACT: "contact",
            SupplierColumns.PHONE: "phone",
            SupplierColumns.EMAIL: "email",
            SupplierColumns.PRODUCTS: "products",
        }
        field = field_map.get(column)
        if not field:
            return False

        if self._edit_handler is None:
            next_row = self._basic_edit(row, field, str(value or ""))
            self._rows[index.row()] = next_row
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
            self.selectedEmailsChanged.emit(self.selected_email_set())
            return True

        ok, next_row, message = self._edit_handler(row, field, str(value or ""))
        if not ok:
            self.rowEditFailed.emit(message or "Não foi possível salvar a edição.")
            return False

        old_email = row.email_norm
        self._rows[index.row()] = next_row
        left = self.index(index.row(), SupplierColumns.PRODUCTS)
        right = self.index(index.row(), SupplierColumns.PHONE)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole])
        if old_email != next_row.email_norm:
            self.selectedEmailsChanged.emit(self.selected_email_set())
        self.rowPersisted.emit(next_row.supplier_key)
        return True

    def _basic_edit(self, row: SupplierRow, field: str, typed: str) -> SupplierRow:
        text = str(typed or "").strip()
        if field == "products":
            parts = [segment.strip() for segment in text.replace(";", ",").replace("|", ",").split(",")]
            parts = [segment for segment in parts if segment]
            return replace(row, products=tuple(parts))
        return replace(row, **{field: text})
