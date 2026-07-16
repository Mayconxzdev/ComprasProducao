from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from app.catalog.product_catalog import ProductCatalog, normalize_catalog_text


_QTY_RE = re.compile(r"\b(\d{1,4})\b")
_THICK_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*mm\b", re.IGNORECASE)
_MEASURE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?(?:\s*m)?\b", re.IGNORECASE)
_NUMBER_TOKEN_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class ParsedQuoteIntent:
    quantity: str = ""
    product: str = ""
    product_id: str = ""
    type: str = ""
    thickness: str = ""
    measure: str = ""
    length: str = ""
    confidence_by_slot: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _pick_match(text_norm: str, options: list[str]) -> tuple[str, float]:
    for value in options:
        norm = normalize_catalog_text(value)
        if norm and norm in text_norm:
            return value, 0.92
    return "", 0.0


def parse_message(text: str, catalog: ProductCatalog) -> ParsedQuoteIntent:
    raw = _clean(text)
    norm = normalize_catalog_text(raw)
    out = ParsedQuoteIntent()
    if not raw:
        out.warnings.append("Mensagem vazia.")
        return out

    qty_match = _QTY_RE.search(raw)
    if qty_match:
        out.quantity = _clean(qty_match.group(1))
        out.confidence_by_slot["quantity"] = 0.8

    resolved = catalog.resolve_product(raw)
    if not resolved:
        simplified = _MEASURE_RE.sub(" ", raw)
        simplified = _THICK_RE.sub(" ", simplified)
        simplified = _NUMBER_TOKEN_RE.sub(" ", simplified)
        simplified = re.sub(r"\s+", " ", simplified).strip()
        if simplified:
            resolved = catalog.resolve_product(simplified)
    if resolved:
        out.product = _clean(resolved.get("canonical"))
        out.product_id = _clean(resolved.get("product_id"))
        out.confidence_by_slot["product"] = 0.95
    else:
        out.warnings.append("Produto não identificado no catálogo.")
        return out

    type_options = catalog.list_types(out.product_id)
    type_value, type_conf = _pick_match(norm, type_options)
    if type_value:
        out.type = type_value
        out.confidence_by_slot["type"] = type_conf

    thick_options = catalog.list_thicknesses(out.product_id, out.type)
    thick_value, thick_conf = _pick_match(norm, thick_options)
    if not thick_value:
        thick_inline = _THICK_RE.search(raw)
        if thick_inline:
            thick_value = _clean(thick_inline.group(0))
            thick_conf = 0.55
    if thick_value:
        out.thickness = thick_value
        out.confidence_by_slot["thickness"] = thick_conf

    measures = catalog.list_measures(out.product_id, out.type, out.thickness)
    measure_options = [_clean(row.get("measure")) for row in measures if _clean(row.get("measure"))]
    measure_value, measure_conf = _pick_match(norm, measure_options)
    if not measure_value:
        measure_inline = _MEASURE_RE.search(raw)
        if measure_inline:
            measure_value = _clean(measure_inline.group(0).replace("×", " x "))
            measure_conf = 0.55
    if measure_value:
        out.measure = measure_value
        out.confidence_by_slot["measure"] = measure_conf

    if out.measure:
        for row in measures:
            measure_text = _clean(row.get("measure"))
            if normalize_catalog_text(measure_text) != normalize_catalog_text(out.measure):
                continue
            length = _clean(row.get("length"))
            if length:
                out.length = length
                out.confidence_by_slot["length"] = 0.75
                break

    return out
