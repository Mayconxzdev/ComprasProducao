from __future__ import annotations

from typing import Any
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal


class TrackingListModel(QAbstractListModel):
    RowRole = Qt.ItemDataRole.UserRole + 1

    rowsChanged = Signal()

    def __init__(self, rows: list[dict] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = list(rows or [])

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role in (self.RowRole, Qt.ItemDataRole.UserRole):
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row.get("subject") or row.get("product_query") or "Cotação")
        return None

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()
        self.rowsChanged.emit()

    def rows(self) -> list[dict]:
        return list(self._rows)
