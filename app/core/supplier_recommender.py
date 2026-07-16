from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .supplier_meta_store_nas import SupplierMeta, supplier_key_from_obj
from .supplier_scoring import SupplierScore, compute_supplier_score


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _email_domain(email: str) -> str:
    value = _clean(email).lower()
    if "@" not in value:
        return ""
    return value.split("@", 1)[1]


@dataclass
class SupplierRecommendation:
    supplier_key: str
    supplier_id: str
    company: str
    email: str
    score: int
    score_breakdown: dict[str, int] = field(default_factory=dict)
    reason: str = ""


def _default_reason(score: SupplierScore) -> str:
    if not score.reasons:
        return "Sem explicacao adicional."
    return "; ".join(score.reasons[:3])


def recommend_suppliers(
    item_context: dict[str, Any],
    *,
    suppliers: Iterable[object],
    history_events: Iterable[dict[str, Any]],
    meta_resolver: Callable[[str], SupplierMeta] | None = None,
    limit: int = 10,
    max_per_domain: int = 2,
) -> list[SupplierRecommendation]:
    ranked: list[tuple[SupplierScore, object]] = []
    for supplier in suppliers:
        supplier_key = supplier_key_from_obj(supplier)
        meta = meta_resolver(supplier_key) if meta_resolver else SupplierMeta(supplier_key=supplier_key)
        score = compute_supplier_score(
            supplier_key=supplier_key,
            item_context=item_context,
            events=history_events,
            supplier=supplier,
            meta=meta,
        )
        if score.total <= 0:
            continue
        ranked.append((score, supplier))

    ranked.sort(
        key=lambda row: (
            -row[0].total,
            -row[0].breakdown.get("history_success", 0),
            _clean(getattr(row[1], "empresa", "")).casefold(),
        )
    )

    out: list[SupplierRecommendation] = []
    per_domain: dict[str, int] = {}
    for score, supplier in ranked:
        if len(out) >= max(1, int(limit or 10)):
            break
        company = _clean(getattr(supplier, "empresa", ""))
        email = _clean(getattr(supplier, "email", ""))
        if not email:
            # Guard-rail no-invention: recommendation requires explicit contact.
            continue
        domain = _email_domain(email)
        if domain:
            count = per_domain.get(domain, 0)
            if count >= max(1, int(max_per_domain or 1)):
                continue
            per_domain[domain] = count + 1
        out.append(
            SupplierRecommendation(
                supplier_key=supplier_key_from_obj(supplier),
                supplier_id=_clean(getattr(supplier, "supplier_id", "")),
                company=company,
                email=email,
                score=score.total,
                score_breakdown=dict(score.breakdown),
                reason=_default_reason(score),
            )
        )
    return out
