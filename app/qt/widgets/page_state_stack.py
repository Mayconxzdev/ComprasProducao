from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget


class PageStateStack(QWidget):
    """Simple Idle/Loading/Error/Empty/Content stack for page state UX."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stack = QStackedWidget(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

        self._content: QWidget | None = None
        self._loading = self._state_label("Carregando...")
        self._error = self._state_label("Falha ao carregar.")
        self._empty = self._state_label("Sem resultados.")
        self._idle = self._state_label("")

        self._idle_idx = self._stack.addWidget(self._idle)
        self._loading_idx = self._stack.addWidget(self._loading)
        self._error_idx = self._stack.addWidget(self._error)
        self._empty_idx = self._stack.addWidget(self._empty)
        self._content_idx = -1

        self.set_idle()

    def _state_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        return label

    def set_content_widget(self, widget: QWidget) -> None:
        if self._content is not None:
            self._stack.removeWidget(self._content)
        self._content = widget
        self._content_idx = self._stack.addWidget(widget)
        self.show_content()

    def set_idle(self, text: str = "") -> None:
        self._idle.setText(text)
        self._stack.setCurrentIndex(self._idle_idx)

    def set_loading(self, text: str = "Carregando...") -> None:
        self._loading.setText(text)
        self._stack.setCurrentIndex(self._loading_idx)

    def set_error(self, text: str = "Falha ao carregar.") -> None:
        self._error.setText(text)
        self._stack.setCurrentIndex(self._error_idx)

    def set_empty(self, text: str = "Sem resultados.") -> None:
        self._empty.setText(text)
        self._stack.setCurrentIndex(self._empty_idx)

    def show_content(self) -> None:
        if self._content_idx >= 0:
            self._stack.setCurrentIndex(self._content_idx)
