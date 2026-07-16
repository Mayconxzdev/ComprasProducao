from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.config import ensure_app_data_dir
from app.core.utils_text import normalize_text


DEFAULT_EX_PANELS: list[dict[str, Any]] = [
    {
        "name": "PAINEL/ELE TR/EX TUV161484 - 445 x 275 x 159 mm",
        "specs": [
            "Partida Estrela/Triângulo",
            "Motor 7,5 CV - 440 V",
            "Cód. Prod.: MXPA14M45H1",
        ],
    },
    {
        "name": "PAINEL/ELE TR/EX TUV161484 - 276 x 276 x 156,5 mm",
        "specs": [
            "Furação NPT",
            "Cód. Prod.: MXPA14P27H1",
            "Para utilização com motor 10 CV - 6 polos",
        ],
    },
    {
        "name": "PAINEL/ELE TR/EX TUV161484 - 170 x 140 x 135 mm",
        "specs": [
            'Furação GI 1/2" NPT',
            "Cód. Prod.: MXPA14P17H1M042",
            "Para utilização com motor trifásico/monofásico",
        ],
    },
    {
        "name": "Painel Elétrico Ex MXPA14P14H1",
        "specs": [
            "Dimensões: 138 x 98 x 95 mm",
            'Furação 3/4"',
            "Trifásico",
            "Grau de proteção IP66",
            "Apropriado para instalação de capacitores e circuito elétrico",
        ],
    },
    {
        "name": "PAINEL/ELE TMX-70GRP",
        "specs": [
            "Ligação Estrela/Triângulo",
            "Partida Estrela/Triângulo para motor 30 CV - 4 polos - 440 V",
            "Cód. Prod.: (se disponível)",
        ],
    },
]


def _library_path() -> Path:
    return ensure_app_data_dir() / "ex_panels_library.json"


def _panel_key(panel: dict[str, Any]) -> str:
    name = normalize_text(str(panel.get("name") or ""))
    specs = "|".join(normalize_text(str(item or "")) for item in panel.get("specs", []) if str(item or "").strip())
    return f"{name}|{specs}"


def _normalize_panel(panel: object) -> dict[str, Any] | None:
    if not isinstance(panel, dict):
        return None
    name = str(panel.get("name") or "").strip()
    specs = [str(item or "").strip() for item in panel.get("specs", []) if str(item or "").strip()]
    if not name or not specs:
        return None
    return {"name": name, "specs": specs}


def load_ex_panel_library() -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = deepcopy(DEFAULT_EX_PANELS)
    seen = {_panel_key(panel) for panel in panels}
    path = _library_path()
    if not path.exists():
        return panels
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return panels
    rows = raw.get("panels") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return panels
    for row in rows:
        panel = _normalize_panel(row)
        if panel is None:
            continue
        key = _panel_key(panel)
        if not key or key in seen:
            continue
        seen.add(key)
        panels.append(panel)
    return panels


def save_ex_panel_library(panels: list[dict[str, Any]]) -> None:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for panel in panels:
        item = _normalize_panel(panel)
        if item is None:
            continue
        key = _panel_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    payload = {"version": 1, "panels": normalized}
    path = _library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def add_ex_panel_to_library(panels: list[dict[str, Any]], panel: dict[str, Any]) -> bool:
    item = _normalize_panel(panel)
    if item is None:
        return False
    key = _panel_key(item)
    if any(_panel_key(existing) == key for existing in panels):
        return False
    panels.append(item)
    save_ex_panel_library(panels)
    return True
