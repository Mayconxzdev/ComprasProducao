from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata
from typing import Any, Dict

from app.core.config import ensure_app_data_dir

USAGE_FILE = "product_usage.json"
USAGE_VERSION = 2

FIELDS = ("product_id", "type", "thickness", "measure", "length")

_WS_RE = re.compile(r"\s+")
_TYPE_GAUGE_SUFFIX_RE = re.compile(r"(?:ch\.?|chapa)\s*(?:fina)?\s*(?:a\s*)?(?:frio|quente)?\s*(\d{1,3})$", re.IGNORECASE)
_TYPE_INOX_RE = re.compile(r"(?:aco)\s*inox\s*(\d{3})\s*([lL]?)$", re.IGNORECASE)
_TYPE_STEEL_RE = re.compile(r"^(?:aco)\s*(\d{3,4})$", re.IGNORECASE)


def _strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _norm_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    normalized = _strip_accents(text).lower()
    normalized = _WS_RE.sub(" ", normalized).strip()

    # Legacy: "Ch. Fina Frio 24" -> "24"
    match = _TYPE_GAUGE_SUFFIX_RE.search(normalized)
    if match:
        return _norm(match.group(1))

    # Legacy inox names: "Aco Inox 304 L" -> "304l"
    match = _TYPE_INOX_RE.search(normalized)
    if match:
        return _norm(f"{match.group(1)}{(match.group(2) or '').upper()}")

    # Legacy steel names: "Aco 1020" -> "1020"
    match = _TYPE_STEEL_RE.search(normalized)
    if match:
        return _norm(match.group(1))

    return _norm(text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _selection_row(selection: Dict[str, Any]) -> Dict[str, str]:
    return {
        "product_id": _norm(selection.get("product_id")),
        "type": _norm_type(selection.get("type")),
        "thickness": _norm(selection.get("thickness")),
        "measure": _norm(selection.get("measure")),
        "length": _norm(selection.get("length")),
    }


def _selection_key(selection: Dict[str, Any]) -> str:
    row = _selection_row(selection)
    return "|".join(row.get(field, "") for field in FIELDS)


@dataclass
class _Record:
    count: int
    last_used_at: str

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "_Record":
        return cls(
            count=max(0, int(raw.get("count", 0) or 0)),
            last_used_at=str(raw.get("last_used_at") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": int(self.count),
            "last_used_at": str(self.last_used_at),
        }


class ProductUsageStore:
    """Local ranking store for quote-item selections."""

    def __init__(self, path: Path | None = None):
        self.path = path or (ensure_app_data_dir() / USAGE_FILE)
        self._data: Dict[str, Any] = {
            "version": USAGE_VERSION,
            "selections": {},
            "products": {},
        }
        self._loaded = False
        self.load()

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        self._data["version"] = int(raw.get("version", 1) or 1)
        self._data["selections"] = dict(raw.get("selections") or {})
        self._data["products"] = dict(raw.get("products") or {})
        if self._data["version"] < USAGE_VERSION:
            self._migrate_to_v2()

    def _migrate_to_v2(self) -> None:
        selections = dict(self._data.get("selections") or {})
        migrated: Dict[str, Dict[str, Any]] = {}

        for key, raw_record in selections.items():
            record = _Record.from_dict(dict(raw_record or {}))
            if record.count <= 0:
                continue

            parts = str(key or "").split("|")
            if len(parts) != len(FIELDS):
                continue

            selection = {FIELDS[i]: parts[i] for i in range(len(FIELDS))}
            migrated_key = _selection_key(selection)
            current = _Record.from_dict(dict(migrated.get(migrated_key) or {}))
            current.count += record.count
            current.last_used_at = max(current.last_used_at, record.last_used_at, key=_parse_ts)
            migrated[migrated_key] = current.to_dict()

        self._data["selections"] = migrated
        self._data["version"] = USAGE_VERSION
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def bump_usage(self, selection: Dict[str, Any]) -> None:
        pid = _norm(selection.get("product_id"))
        if not pid:
            return
        now = _now_iso()

        selections = self._data.setdefault("selections", {})
        key = _selection_key(selection)
        rec = _Record.from_dict(dict(selections.get(key) or {}))
        rec.count += 1
        rec.last_used_at = now
        selections[key] = rec.to_dict()

        products = self._data.setdefault("products", {})
        p_rec = _Record.from_dict(dict(products.get(pid) or {}))
        p_rec.count += 1
        p_rec.last_used_at = now
        products[pid] = p_rec.to_dict()

        self._data["version"] = USAGE_VERSION
        self.save()

    def rank_boost(self, selection: Dict[str, Any]) -> float:
        pid = _norm(selection.get("product_id"))
        if not pid:
            return 0.0

        products = self._data.get("products", {})
        selections = self._data.get("selections", {})
        product_rec = _Record.from_dict(dict(products.get(pid) or {}))

        # Base product ranking (used by product suggestions).
        score = float(product_rec.count * 100)
        if product_rec.last_used_at:
            score += self._recency_bonus(product_rec.last_used_at)

        # Additional boost for partial/full selection match.
        filters = _selection_row(selection)
        for key, raw in selections.items():
            rec = _Record.from_dict(dict(raw or {}))
            if rec.count <= 0:
                continue
            parts = key.split("|")
            if len(parts) != len(FIELDS):
                continue
            row = {FIELDS[i]: parts[i] for i in range(len(FIELDS))}
            if not self._matches_filter(row, filters):
                continue
            score += float(rec.count * 1000)
            score += self._recency_bonus(rec.last_used_at)

        return score

    def _matches_filter(self, row: Dict[str, str], filters: Dict[str, str]) -> bool:
        for field in FIELDS:
            want = filters.get(field, "")
            if want and row.get(field, "") != want:
                return False
        return True

    def _recency_bonus(self, ts: str) -> float:
        dt = _parse_ts(ts)
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        # Smooth decay: today gets ~50 points, then slowly decays.
        return 50.0 / (1.0 + age_days)
