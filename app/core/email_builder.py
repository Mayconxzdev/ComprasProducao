from __future__ import annotations
from typing import List
from .models import QuoteItem


def build_default_subject(prefix: str, product_query: str) -> str:
    prefix = (prefix or "Cota\u00e7\u00e3o").strip()
    product = (product_query or "").strip()
    if product:
        return f"{prefix} - {product}"
    return prefix


def build_default_body(items: List[QuoteItem], observations: str = "") -> str:
    items_text = "\n".join(i.line_text.strip() for i in items if i.line_text.strip())
    body = (
        "Favor cotar o material abaixo, informando forma de pagamento, prazo de entrega e disponibilidade em estoque:\n\n"
        f"{items_text}\n\n"
    )
    if observations and observations.strip():
        body += f"Observa\u00e7\u00f5es:\n{observations.strip()}\n\n"
    body += "Obrigado."
    return body
