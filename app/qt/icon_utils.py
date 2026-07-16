from __future__ import annotations

from functools import lru_cache

_ICON_NAMES = {
    "overview": "fa5s.home",
    "new_request": "fa5s.plus-circle",
    "material": "fa5s.box",
    "ex_panels": "fa5s.shield-alt",
    "freight": "fa5s.truck",
    "purchase_order": "fa5s.clipboard-check",
    "history": "fa5s.inbox",
    "suppliers": "fa5s.building",
    "models": "fa5s.file-alt",
    "settings": "fa5s.cog",
    "answered": "fa5s.check-circle",
    "pending": "fa5s.clock",
    "partial": "fa5s.adjust",
    "failed": "fa5s.exclamation-triangle",
    "archived": "fa5s.archive",
    "refresh": "fa5s.sync-alt",
    "send": "fa5s.paper-plane",
    "mail_open": "fa5s.envelope-open-text",
}

_FALLBACK = {
    "overview": "⌂",
    "new_request": "+",
    "material": "□",
    "ex_panels": "✓",
    "freight": "▣",
    "purchase_order": "☷",
    "history": "✉",
    "suppliers": "▦",
    "models": "▤",
    "settings": "⚙",
    "answered": "✓",
    "pending": "◷",
    "partial": "◐",
    "failed": "!",
    "archived": "▤",
    "refresh": "↻",
    "send": "➤",
    "mail_open": "✉",
}

def get_icon_char(key: str) -> str:
    return _FALLBACK.get(str(key or ""), "•")

@lru_cache(maxsize=128)
def _cached_icon(name: str, color: str, scale_factor: float):
    import qtawesome as qta  # optional dependency installed by .bat/requirements
    return qta.icon(name, color=color, scale_factor=scale_factor)

def get_icon(key: str, *, color: str = "#2563eb", scale_factor: float = 1.0):
    try:
        name = _ICON_NAMES.get(str(key or ""), "fa5s.circle")
        return _cached_icon(name, color, float(scale_factor))
    except Exception:
        try:
            from PySide6.QtGui import QIcon
            return QIcon()
        except Exception:
            return None
