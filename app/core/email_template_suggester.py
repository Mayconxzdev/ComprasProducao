from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class EmailSuggestion:
    subject: str
    body: str
    template_key: str
    rationale: list[str] = field(default_factory=list)


def _normalize_items(items: list[str]) -> list[str]:
    out: list[str] = []
    for line in items or []:
        text = _clean(line)
        if text:
            out.append(text)
    return out


def suggest_email_template(quote_context: dict[str, Any]) -> EmailSuggestion:
    product_query = _clean(quote_context.get("product_query"))
    urgency = _clean(quote_context.get("urgency")).lower()
    observations = _clean(quote_context.get("observations"))
    items = _normalize_items(list(quote_context.get("items") or []))

    if urgency in {"alta", "urgente", "hoje"}:
        template_key = "urgent_rfq"
        prefix = "URGENTE"
    else:
        template_key = "standard_rfq"
        prefix = "Cotacao"

    if product_query:
        subject = f"{prefix} - {product_query}"
    else:
        subject = f"{prefix} - Solicitação de proposta"

    lines: list[str] = []
    lines.append("Prezados,")
    lines.append("")
    lines.append("Solicito cotação para os itens abaixo:")
    lines.append("")
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx:02d}. {item}")

    if not items:
        # Guard-rail: no invention; keep generic placeholder when no explicit item exists.
        lines.append("- (itens não informados)")

    lines.append("")
    lines.append("Favor informar:")
    lines.append("- preço unitário")
    lines.append("- prazo de entrega")
    lines.append("- condição de pagamento")

    if observations:
        lines.append("")
        lines.append("Observações:")
        lines.append(observations)

    lines.append("")
    lines.append("Agradeço o retorno.")

    rationale: list[str] = []
    if items:
        rationale.append(f"{len(items)} item(ns) incluído(s)")
    if urgency in {"alta", "urgente", "hoje"}:
        rationale.append("modo urgente")
    if observations:
        rationale.append("observações preservadas do usuário")

    return EmailSuggestion(
        subject=subject,
        body="\n".join(lines).strip(),
        template_key=template_key,
        rationale=rationale,
    )
