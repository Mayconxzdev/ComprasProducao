from __future__ import annotations

from pathlib import Path

_loaded = False

try:
    from . import app_resources_rc  # noqa: F401

    _loaded = True
except Exception:
    _loaded = False


def resource_root() -> Path:
    return Path(__file__).resolve().parent


def resources_loaded() -> bool:
    return bool(_loaded)
