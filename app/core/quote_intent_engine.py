from __future__ import annotations

from dataclasses import dataclass, field

from app.catalog.product_catalog import ProductCatalog

from .quote_chat_parser import ParsedQuoteIntent, parse_message


@dataclass
class QuoteIntentDecision:
    parsed: ParsedQuoteIntent
    confirmed: dict[str, str] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    summary: str = ""


def interpret_message(
    text: str,
    *,
    catalog: ProductCatalog,
    min_confidence: float = 0.7,
) -> QuoteIntentDecision:
    parsed = parse_message(text, catalog)
    confirmed: dict[str, str] = {}
    pending: dict[str, str] = {}

    for slot in ("quantity", "product", "type", "thickness", "measure", "length"):
        value = str(getattr(parsed, slot, "") or "").strip()
        if not value:
            continue
        confidence = float(parsed.confidence_by_slot.get(slot, 0.0))
        if confidence >= float(min_confidence):
            confirmed[slot] = value
        else:
            pending[slot] = value

    if not confirmed and parsed.warnings:
        summary = parsed.warnings[0]
    elif pending:
        summary = "Confirme os campos com baixa confiança antes de aplicar."
    else:
        summary = "Campos prontos para aplicar na cotação."

    return QuoteIntentDecision(
        parsed=parsed,
        confirmed=confirmed,
        pending=pending,
        summary=summary,
    )
