from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit, QStyledItemDelegate, QWidget


class SupplierProductsDelegate(QStyledItemDelegate):
    def __init__(self, *, suggestions: Sequence[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suggestions: list[str] = list(suggestions or [])

    def set_suggestions(self, suggestions: Sequence[str]) -> None:
        self._suggestions = [str(value).strip() for value in suggestions if str(value).strip()]

    def createEditor(self, parent: QWidget, option, index):  # noqa: N802
        editor = QLineEdit(parent)
        editor.setPlaceholderText("ex: CHAPA FINA FRIO, TUBO ACO INOX")
        completer = QCompleter(editor)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setModel(QStringListModel(self._suggestions, completer))
        editor.setCompleter(completer)
        return editor

    def setEditorData(self, editor: QWidget, index) -> None:  # noqa: N802
        if not isinstance(editor, QLineEdit):
            return
        value = str(index.model().data(index, Qt.ItemDataRole.EditRole) or "")
        editor.setText(value)

    def setModelData(self, editor: QWidget, model, index) -> None:  # noqa: N802
        if not isinstance(editor, QLineEdit):
            return
        model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
