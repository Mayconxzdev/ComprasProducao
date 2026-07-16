from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Sequence

from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from app.qt.theme import ensure_valid_font


_NAVIGATION_KEYS = {"up", "down", "return", "enter", "tab", "escape"}
_WS_RE = re.compile(r"\s+")
_MEASURE_X_RE = re.compile(r"(?<=\d)\s*[xX\u00D7]\s*(?=\d)")


@dataclass(frozen=True)
class SuggestionOption:
    label: str
    value: str
    payload: Any = None


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _normalize(value: str | None) -> str:
    text = _clean(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\u00D7", "x")
    text = _MEASURE_X_RE.sub(" x ", text)
    text = re.sub(r"[-_/\\]", " ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _dedupe_options(options: Iterable[SuggestionOption]) -> List[SuggestionOption]:
    seen: set[tuple[str, str]] = set()
    out: List[SuggestionOption] = []
    for option in options:
        key = (_normalize(option.label), _normalize(option.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(option)
    return out


def resolve_suggestions(
    options: Sequence[SuggestionOption],
    query: str,
    *,
    force: bool,
    limit: int = 12,
) -> List[SuggestionOption]:
    max_items = max(1, int(limit or 12))
    base_all = _dedupe_options(options)
    fallback = base_all[:max_items]
    q = _normalize(query)

    filtered: List[SuggestionOption] = []
    if q:
        prefix: List[SuggestionOption] = []
        contains: List[SuggestionOption] = []
        for option in base_all:
            label_norm = _normalize(option.label)
            value_norm = _normalize(option.value)
            if label_norm.startswith(q) or value_norm.startswith(q):
                prefix.append(option)
            elif q in label_norm or q in value_norm:
                contains.append(option)
        filtered = (prefix + contains)[:max_items]

    if filtered:
        return filtered
    if force:
        return fallback
    return []


def should_schedule_refresh(
    *,
    suppress_events: bool,
    key: str | None = None,
    keysym: str | None = None,
) -> bool:
    if suppress_events:
        return False
    key_value = key if key is not None else keysym
    if (key_value or "").strip().lower() in _NAVIGATION_KEYS:
        return False
    return True


def should_hide_popup_for_focus_change(*, focus_inside_field: bool) -> bool:
    return not focus_inside_field


def resolve_commit_value(
    typed_value: str,
    options: Sequence[SuggestionOption],
    *,
    allow_manual: bool,
    fallback_value: str = "",
) -> tuple[str, bool, SuggestionOption | None]:
    typed = _clean(typed_value)
    if not typed:
        return "", False, None

    typed_norm = _normalize(typed)
    for option in options:
        if _normalize(option.label) == typed_norm or _normalize(option.value) == typed_norm:
            return option.label, True, option

    if allow_manual:
        return typed, False, None

    fallback = _clean(fallback_value)
    if fallback:
        return fallback, True, None
    if options:
        return options[0].label, True, options[0]
    return "", False, None


class SmartSuggestLineEdit(QWidget):
    """
    Standard Qt autocomplete field:
    QLineEdit + arrow button + QCompleter.
    """

    committed = Signal(str, bool, object)
    changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        debounce_ms: int = 150,
        allow_manual: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._provider: Callable[[str, bool], List[SuggestionOption]] = lambda _q, _force: []
        self._allow_manual = bool(allow_manual)
        self._suppress_events = False
        self._visible_options: List[SuggestionOption] = []
        self._last_valid_text = ""
        self._last_valid_payload: Any = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(debounce_ms or 150)))
        self._timer.timeout.connect(self._refresh_suggestions)

        self._entry = QLineEdit(self)
        self._entry.setFont(ensure_valid_font(self._entry.font()))
        self._entry.setClearButtonEnabled(True)
        self._entry.installEventFilter(self)
        self._entry.textEdited.connect(self._on_text_edited)

        self._button = QToolButton(self)
        self._button.setFont(ensure_valid_font(self._button.font()))
        self._button.setText("▼")
        self._button.setObjectName("suggestArrow")
        self._button.clicked.connect(self._on_arrow_click)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)

        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.activated[str].connect(self._on_activated)
        popup = self._completer.popup()
        popup.setObjectName("suggestPopup")
        popup.setFont(ensure_valid_font(popup.font()))
        popup.setTextElideMode(Qt.TextElideMode.ElideNone)
        popup.setUniformItemSizes(False)
        popup.setStyleSheet(
            "QListView#suggestPopup{padding:2px;}"
            "QListView#suggestPopup::item{padding:6px 8px;min-height:22px;}"
        )
        self._entry.setCompleter(self._completer)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._entry, 1)
        layout.addWidget(self._button, 0)

    def set_provider(self, provider: Callable[[str, bool], List[SuggestionOption]]) -> None:
        self._provider = provider

    def set_manual_allowed(self, allow_manual: bool) -> None:
        self._allow_manual = bool(allow_manual)

    def set_placeholder_text(self, text: str) -> None:
        self._entry.setPlaceholderText(str(text or ""))

    def set_clear_button_enabled(self, enabled: bool) -> None:
        self._entry.setClearButtonEnabled(bool(enabled))

    def set_entry_alignment(self, alignment: Qt.AlignmentFlag | Qt.Alignment) -> None:
        self._entry.setAlignment(Qt.AlignmentFlag(alignment))

    def set_value(self, value: str, *, programmatic: bool = True) -> None:
        text = value or ""
        if programmatic:
            self._suppress_events = True
        try:
            self._entry.setText(text)
            if programmatic and not self._entry.hasFocus():
                self._entry.setCursorPosition(0)
                self._entry.deselect()
            self._entry.setToolTip(text)
        finally:
            if programmatic:
                self._suppress_events = False

    def value(self) -> str:
        return self._entry.text().strip()

    def focus_entry(self) -> None:
        self._entry.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_suggestions(self, force: bool = False) -> None:
        query = "" if force else self.value()
        options = list(self._provider(query, force)) if self._provider else []
        self._visible_options = options
        labels = [opt.label for opt in options]
        self._completer.setModel(QStringListModel(labels, self._completer))
        # Provider already decides which options to show; avoid extra filtering by entry text.
        self._completer.setCompletionPrefix("")
        popup = self._popup()
        if popup is None:
            return
        popup.setFont(ensure_valid_font(popup.font()))
        if not labels:
            popup.hide()
            return
        popup.setMinimumWidth(self._popup_target_width(labels))
        self._completer.complete()

    def _popup_target_width(self, labels: Sequence[str]) -> int:
        entry_width = max(140, int(self.width()))
        if not labels:
            return entry_width
        metrics = QFontMetrics(self._entry.font())
        longest = max(metrics.horizontalAdvance(str(label or "")) for label in labels)
        # Margem para padding/borda e eventual scrollbar.
        content_width = longest + 48
        return max(entry_width, content_width)

    def _on_arrow_click(self) -> None:
        self.focus_entry()
        self.show_suggestions(force=True)

    def _on_text_edited(self, text: str) -> None:
        self._entry.setToolTip(str(text))
        self.changed.emit(str(text))
        if should_schedule_refresh(suppress_events=self._suppress_events, key=None):
            self._timer.start()

    def _refresh_suggestions(self) -> None:
        self.show_suggestions(force=False)

    def _on_activated(self, label: str) -> None:
        picked = self._pick_option_from_label(label)
        if picked is None:
            self._commit_current(from_catalog=False, payload=None)
            return
        self.set_value(picked.label, programmatic=True)
        payload = picked.payload if picked.payload is not None else picked.value
        self._last_valid_text = picked.label
        self._last_valid_payload = payload
        self.committed.emit(picked.label, True, payload)

    def _pick_option_from_label(self, label: str) -> SuggestionOption | None:
        wanted = _normalize(label)
        for option in self._visible_options:
            if _normalize(option.label) == wanted:
                return option
        return None

    def _commit_current(self, *, from_catalog: bool, payload: Any) -> None:
        typed = self.value()
        if from_catalog:
            self.committed.emit(typed, True, payload)
            return

        if self._allow_manual:
            self.committed.emit(typed, False, None)
            return

        option = self._pick_option_from_label(typed)
        if option is None and self._visible_options:
            option = self._visible_options[0]
        if option is None and self._last_valid_text:
            self.set_value(self._last_valid_text, programmatic=True)
            self.committed.emit(self._last_valid_text, True, self._last_valid_payload)
            return
        if option is None:
            self.set_value("", programmatic=True)
            self.committed.emit("", True, None)
            return

        self.set_value(option.label, programmatic=True)
        resolved_payload = option.payload if option.payload is not None else option.value
        self._last_valid_text = option.label
        self._last_valid_payload = resolved_payload
        self.committed.emit(option.label, True, resolved_payload)

    def _focus_inside_cluster(self) -> bool:
        focus_widget = QApplication.focusWidget()
        if focus_widget is None:
            return False
        popup = self._popup()
        if focus_widget is self._entry or focus_widget is self._button:
            return True
        if focus_widget is self or self.isAncestorOf(focus_widget):
            return True
        if popup is not None and (focus_widget is popup or popup.isAncestorOf(focus_widget)):
            return True
        return False

    def _hide_popup_if_focus_outside(self) -> None:
        popup = self._popup()
        if popup is not None and not self._focus_inside_cluster():
            popup.hide()

    def _popup(self) -> QWidget | None:
        try:
            return self._completer.popup()
        except RuntimeError:
            # Can happen during shutdown/focus transitions while widget tree is
            # being destroyed; avoid crashing in eventFilter callbacks.
            return None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self._entry:
            return super().eventFilter(watched, event)

        popup = self._popup()
        if event.type() == QEvent.Type.KeyPress:
            key_event = event  # type: ignore[assignment]
            if not isinstance(key_event, QKeyEvent):
                return super().eventFilter(watched, event)

            key = key_event.key()
            if key == Qt.Key.Key_Down:
                if popup is not None and not popup.isVisible():
                    self.show_suggestions(force=True)
                    return True
                return False

            if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self._commit_current(from_catalog=False, payload=None)
                return True

            if key == Qt.Key.Key_Tab:
                self._commit_current(from_catalog=False, payload=None)
                return False

            if key == Qt.Key.Key_Escape:
                if popup is not None:
                    popup.hide()
                return True

        if event.type() == QEvent.Type.FocusOut:
            # Delay one event-loop tick so focus can move to popup/arrow without
            # collapsing suggestions immediately.
            QTimer.singleShot(0, self._hide_popup_if_focus_outside)

        return super().eventFilter(watched, event)
