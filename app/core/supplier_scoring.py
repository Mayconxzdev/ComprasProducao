from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .supplier_meta_store_nas import SupplierMeta
from .utils_text import normalize_text


@dataclass
class ScoreBreakdown:
    total: int
    active: int
    email: int
    exact_item: int
    recency: int
    favorite: int
    text_match: int


@dataclass
class SupplierScore:
    supplier_key: str
    total: int
    raw_total: float
    breakdown: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    sample_size: int = 0


def has_valid_email(supplier) -> bool:
    email = str(getattr(supplier, "email", "") or "")
    return "@" in email and "." in email


def supplier_has_item_id(supplier, item_id: str) -> bool:
    if not item_id:
        return False
    items = getattr(supplier, "items", None) or []
    for it in items:
        if str(getattr(it, "item_id", "") or "") == item_id:
            return True
    return False


def _parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = (
        text,
        text.replace("Z", "+00:00"),
        text.replace("/", "-"),
    )
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def _recency_points(last_used_at: str) -> int:
    dt = _parse_dt(last_used_at)
    if dt is None:
        return 0
    days = max(0, int((datetime.now(timezone.utc) - dt).days))
    if days <= 3:
        return 20
    if days <= 7:
        return 15
    if days <= 30:
        return 10
    if days <= 90:
        return 6
    if days <= 180:
        return 3
    return 1


def _supplier_products_text(supplier: object) -> str:
    chunks: list[str] = []
    raw_products = str(getattr(supplier, "material_produto", "") or "")
    if raw_products:
        chunks.append(raw_products)
    items = getattr(supplier, "items", None) or []
    for item in items:
        for attr in ("item", "item_id"):
            value = str(getattr(item, attr, "") or "").strip()
            if value:
                chunks.append(value)
    return " ".join(chunks)


def _iter_matching_events(
    events: Iterable[dict[str, Any]],
    *,
    supplier_key: str,
    supplier_email: str,
) -> list[dict[str, Any]]:
    wanted_email = normalize_text(supplier_email)
    wanted_key = normalize_text(supplier_key)
    if not wanted_email and not wanted_key:
        return []
    out: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        recipients = event.get("recipients") or []
        matched = False
        for recipient in recipients:
            if not isinstance(recipient, dict):
                continue
            rid = normalize_text(recipient.get("supplier_id") or "")
            remail = normalize_text(recipient.get("email") or "")
            if wanted_key and rid and rid == wanted_key:
                matched = True
                break
            if wanted_email and remail and remail == wanted_email:
                matched = True
                break
        if matched:
            out.append(event)
    return out


def _safe_ratio(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return float(num) / float(den)


def _contains_context_product(item_context: dict[str, Any], supplier: object) -> bool:
    wanted = normalize_text(item_context.get("product") or item_context.get("product_query") or "")
    if not wanted:
        return False
    target = normalize_text(_supplier_products_text(supplier))
    if not target:
        return False
    tokens = [token for token in wanted.split() if token]
    if not tokens:
        return False
    return all(token in target for token in tokens)


def compute_supplier_score(
    supplier_key: str,
    item_context: dict[str, Any],
    events: Iterable[dict[str, Any]],
    *,
    supplier: object | None = None,
    meta: SupplierMeta | None = None,
    base_match_score: int = 0,
) -> SupplierScore:
    supplier = supplier or object()
    meta = meta or SupplierMeta(supplier_key=supplier_key)

    email = str(getattr(supplier, "email", "") or "")
    matched_events = _iter_matching_events(events, supplier_key=supplier_key, supplier_email=email)

    sent_events = [ev for ev in matched_events if str(ev.get("event_type") or "") in {"smtp_send", "followup_sent"}]
    success_events = [ev for ev in sent_events if str(ev.get("status") or "") == "sent_smtp_ok"]
    fail_events = [ev for ev in sent_events if str(ev.get("status") or "") == "sent_smtp_fail"]

    success_ratio = _safe_ratio(len(success_events), len(sent_events))
    fail_ratio = _safe_ratio(len(fail_events), len(sent_events))

    breakdown: dict[str, int] = {}
    reasons: list[str] = []

    status = str(meta.status or "ATIVO").upper()
    if status == "ATIVO":
        breakdown["status"] = 12
        reasons.append("Fornecedor ativo")
    else:
        breakdown["status"] = -35
        reasons.append("Fornecedor inativo")

    if has_valid_email(supplier):
        breakdown["email"] = 12
    else:
        breakdown["email"] = -40
        reasons.append("Sem e-mail valido")

    completeness = 0
    if str(getattr(supplier, "empresa", "") or "").strip():
        completeness += 4
    if str(getattr(supplier, "telefone", "") or "").strip():
        completeness += 4
    if str(getattr(supplier, "contato_nome", "") or getattr(supplier, "contato", "") or "").strip():
        completeness += 4
    if _supplier_products_text(supplier).strip():
        completeness += 4
    breakdown["completeness"] = completeness

    breakdown["recency"] = _recency_points(meta.last_used_at)
    if breakdown["recency"] >= 10:
        reasons.append("Uso recente")

    breakdown["favorite"] = 8 if bool(meta.is_favorite) else 0
    if breakdown["favorite"]:
        reasons.append("Fornecedor favorito")

    breakdown["history_success"] = int(round(success_ratio * 24))
    breakdown["history_fail"] = -int(round(fail_ratio * 20))
    if sent_events:
        reasons.append(f"Historico SMTP: {len(success_events)}/{len(sent_events)} sucesso")

    breakdown["product_fit"] = 12 if _contains_context_product(item_context, supplier) else 0
    if breakdown["product_fit"]:
        reasons.append("Produto aderente ao contexto")

    breakdown["text_match"] = max(0, min(12, int(base_match_score)))

    raw_total = 45.0 + float(sum(breakdown.values()))
    total = max(0, min(100, int(round(raw_total))))
    return SupplierScore(
        supplier_key=str(supplier_key or "").strip(),
        total=total,
        raw_total=raw_total,
        breakdown=breakdown,
        reasons=reasons,
        sample_size=len(sent_events),
    )


def score_supplier(
    supplier,
    *,
    meta: SupplierMeta,
    base_match_score: int = 0,
    exact_item_id: str = "",
) -> ScoreBreakdown:
    active = 120 if (meta.status or "ATIVO").upper() == "ATIVO" else -1000
    email = 35 if has_valid_email(supplier) else -50
    exact_item = 45 if (exact_item_id and supplier_has_item_id(supplier, exact_item_id)) else 0
    favorite = 20 if bool(meta.is_favorite) else 0
    recency = _recency_points(meta.last_used_at)
    text = max(0, int(base_match_score)) * 8
    total = active + email + exact_item + favorite + recency + text
    return ScoreBreakdown(
        total=total,
        active=active,
        email=email,
        exact_item=exact_item,
        recency=recency,
        favorite=favorite,
        text_match=text,
    )
