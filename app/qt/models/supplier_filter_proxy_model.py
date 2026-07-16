from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt

from app.core.utils_text import normalize_text

from .supplier_table_model import SupplierColumns, SupplierRow


class SupplierFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filter_text = ""
        self._filter_tokens: list[str] = []
        # DynamicSortFilter ligado recalcula filtro/ordem em muitas mudanças pequenas.
        # Para busca digitada, invalidamos explicitamente no debounce da UI.
        self.setDynamicSortFilter(False)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filter_text(self, text: str) -> None:
        normalized = normalize_text(text)
        if normalized == self._filter_text:
            return
        self._filter_text = normalized
        self._filter_tokens = [token for token in normalized.split(" ") if token]
        try:
            self.invalidateFilter()
        except AttributeError:  # Qt antigo
            self.invalidate()

    def filter_text(self) -> str:
        return self._filter_text

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        if not self._filter_tokens:
            return True
        model = self.sourceModel()
        if model is None:
            return True

        index = model.index(source_row, SupplierColumns.COMPANY, source_parent)
        payload = model.data(index, Qt.ItemDataRole.UserRole)
        if isinstance(payload, SupplierRow):
            normalized_blob = payload.search_blob_norm
        else:
            parts: list[str] = []
            for col in (
                SupplierColumns.COMPANY,
                SupplierColumns.CONTACT,
                SupplierColumns.PHONE,
                SupplierColumns.EMAIL,
                SupplierColumns.PRODUCTS,
            ):
                parts.append(str(model.data(model.index(source_row, col, source_parent), Qt.ItemDataRole.DisplayRole) or ""))
            normalized_blob = normalize_text(" ".join(parts))
        return all(token in normalized_blob for token in self._filter_tokens)

    def lessThan(self, left, right) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None:
            return False

        if left.column() == SupplierColumns.SCORE:
            try:
                left_value = int(model.data(left, Qt.ItemDataRole.DisplayRole) or 0)
            except Exception:
                left_value = 0
            try:
                right_value = int(model.data(right, Qt.ItemDataRole.DisplayRole) or 0)
            except Exception:
                right_value = 0
            return left_value < right_value

        left_value = str(model.data(left, Qt.ItemDataRole.DisplayRole) or "")
        right_value = str(model.data(right, Qt.ItemDataRole.DisplayRole) or "")
        return left_value.casefold() < right_value.casefold()
