from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"\d+\s*/\s*\d+|\d+(?:[.,]\d+)?[A-Za-zÀ-ÿ]*|[A-Za-zÀ-ÿ]+")
_STOP_WORDS = {
    "ch",
    "chapa",
    "tubo",
    "aco",
    "inox",
    "fina",
    "frio",
    "quente",
    "galvanizada",
    "cantoneira",
    "barra",
    "chata",
    "redonda",
    "de",
    "do",
    "da",
    "para",
    "p",
}


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _normalize_text(value: str | None) -> str:
    text = _clean(value).replace("×", "x")
    text = _strip_accents(text).lower()
    text = re.sub(r"[()\[\]{}]", " ", text)
    text = text.replace("-", " ")
    text = _WS_RE.sub(" ", text)
    return text.strip()


def normalize_tokens(text: str) -> List[str]:
    """
    Tokenize and normalize text for robust dedupe/compact matching.
    """
    norm = _normalize_text(text)
    out: List[str] = []
    for match in _TOKEN_RE.finditer(norm):
        token = re.sub(r"\s*/\s*", "/", match.group(0).strip())
        token = token.strip(" ._")
        if token:
            out.append(token)
    return out


def _token_infos(text: str) -> List[Tuple[str, str]]:
    raw_text = _clean(text).replace("×", "x")
    out: List[Tuple[str, str]] = []
    for match in _TOKEN_RE.finditer(raw_text):
        display = re.sub(r"\s*/\s*", "/", match.group(0).strip())
        display = _WS_RE.sub(" ", display).strip(" ._")
        if not display:
            continue
        norm_tokens = normalize_tokens(display)
        if not norm_tokens:
            continue
        out.append((display, norm_tokens[0]))
    return out


def _cleanup_text(text: str) -> str:
    value = _clean(text).replace("×", "x")
    if not value:
        return ""
    value = re.sub(r"\s*/\s*", "/", value)
    value = re.sub(r"\b(30[46])\s*[lL]\b", lambda m: f"{m.group(1)}L", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(\d+)\s+([lL])\b", lambda m: f"{m.group(1)}L", value)
    value = _WS_RE.sub(" ", value).strip(" |-")
    return value


def _remove_exact_prefix(source: str, prefix: str) -> str:
    source_clean = _clean(source)
    prefix_clean = _clean(prefix)
    if not source_clean or not prefix_clean:
        return source_clean
    if source_clean.casefold().startswith(prefix_clean.casefold()):
        trimmed = source_clean[len(prefix_clean) :].lstrip(" -|:/")
        return trimmed or source_clean
    return source_clean


def _remove_known_tokens(text: str, known_tokens: Iterable[str]) -> str:
    known = set(known_tokens)
    if not known:
        return _clean(text)
    kept = [display for display, norm in _token_infos(text) if norm not in known]
    return _cleanup_text(" ".join(kept))


def compact_spec(product_canonical: str, type_long: str, is_manual_type: bool = False) -> str:
    """
    Build a compact, non-redundant type/spec text.

    Manual value: only light prefix de-duplication.
    Catalog value: remove product/stopword overlap aggressively with fallbacks.
    """
    product = _clean(product_canonical)
    type_value = _clean(type_long)
    if not type_value:
        return ""

    if is_manual_type:
        return _cleanup_text(_remove_exact_prefix(type_value, product))

    product_tokens = set(normalize_tokens(product))
    filtered_parts = [
        display
        for display, norm in _token_infos(type_value)
        if norm not in product_tokens and norm not in _STOP_WORDS
    ]

    if filtered_parts:
        spec = _cleanup_text(" ".join(filtered_parts))
    else:
        spec = _cleanup_text(_remove_exact_prefix(type_value, product))

    if not spec:
        spec = _cleanup_text(type_value)
    return spec


def dedupe_segments(segments: List[str]) -> List[str]:
    """
    Remove redundant segments by semantic token overlap.
    """
    out: List[str] = []
    known_tokens: set[str] = set()
    for idx, segment in enumerate(segments):
        candidate = _clean(segment)
        if not candidate:
            continue

        tokens = set(normalize_tokens(candidate))
        if tokens and known_tokens and tokens.issubset(known_tokens):
            continue

        # Partial token pruning is intentionally limited to spec segment (index 1)
        # to avoid mutilating measure/length texts such as "MEDIDA MANUAL".
        if idx == 1 and tokens and known_tokens and tokens.intersection(known_tokens):
            trimmed = _remove_known_tokens(candidate, known_tokens)
            if trimmed:
                candidate = trimmed
                tokens = set(normalize_tokens(candidate))
            else:
                continue

        if tokens and known_tokens and tokens.issubset(known_tokens):
            continue

        out.append(candidate)
        known_tokens.update(tokens)
    return out


def _hint_token(product_canonical: str, type_long: str, base_spec: str) -> str:
    excluded = set(normalize_tokens(product_canonical))
    excluded.update(normalize_tokens(base_spec))
    excluded.update(_STOP_WORDS)
    for display, norm in _token_infos(type_long):
        if norm in excluded:
            continue
        if re.fullmatch(r"[A-Za-zÀ-ÿ]{1,6}", display):
            if len(display) <= 2:
                return display.upper()
            return display[0].upper() + display[1:].lower()
        if display:
            return display
    return ""


def _next_unique_label(label: str, used: set[str], fallback_seed: str) -> str:
    base = _clean(label) or _clean(fallback_seed) or "Tipo"
    candidate = base
    idx = 2
    while candidate in used:
        candidate = f"{base} ({idx})"
        idx += 1
    used.add(candidate)
    return candidate


def build_type_display_map(product_canonical: str, type_longs: List[str]) -> Dict[str, Dict[str, str] | List[str]]:
    """
    Build UI mapping for compact type display while preserving long type internally.
    """
    ordered_longs = [_clean(v) for v in type_longs if _clean(v)]
    base_by_long = {
        long_value: compact_spec(product_canonical, long_value, is_manual_type=False) or _cleanup_text(long_value)
        for long_value in ordered_longs
    }

    by_base: Dict[str, List[str]] = defaultdict(list)
    for long_value in ordered_longs:
        base_key = _normalize_text(base_by_long[long_value])
        by_base[base_key].append(long_value)

    long_to_display: Dict[str, str] = {}
    used_labels: set[str] = set()

    for base_key, colliding in by_base.items():
        base_label = _clean(base_by_long.get(colliding[0]) if colliding else "")
        if len(colliding) == 1:
            only = colliding[0]
            long_to_display[only] = _next_unique_label(base_label, used_labels, only)
            continue

        hints = [_hint_token(product_canonical, long_value, base_label) for long_value in colliding]
        hints_unique = all(hints) and len(set(h.casefold() for h in hints)) == len(hints)

        for idx, long_value in enumerate(colliding):
            if hints_unique:
                candidate = f"{base_label} ({hints[idx]})"
            else:
                candidate = f"{base_label} ({chr(ord('A') + idx)})"
            long_to_display[long_value] = _next_unique_label(candidate, used_labels, long_value)

    display_to_long: Dict[str, str] = {}
    display_values: List[str] = []
    for long_value in ordered_longs:
        label = long_to_display.get(long_value) or _next_unique_label(
            base_by_long.get(long_value, ""), set(display_to_long.keys()), long_value
        )
        display_values.append(label)
        display_to_long[label] = long_value

    return {
        "display_values": display_values,
        "display_to_long": display_to_long,
        "long_to_display": long_to_display,
    }


def format_item(
    group_name: str | None,
    canonical_product: str | None,
    type: str | None,
    thickness: str | None,
    measure: str | None,
    length: str | None,
    *,
    is_manual_type: bool = False,
) -> str:
    """
    Build compact quote item text using pipe separator.

    `group_name` is intentionally ignored in v1.1 output.
    """
    _ = group_name  # Kept for backward-compatible call sites.

    parts = [
        _clean(canonical_product),
        compact_spec(_clean(canonical_product), _clean(type), is_manual_type=is_manual_type),
        _clean(thickness),
        _clean(measure),
        _clean(length),
    ]
    compact = dedupe_segments(parts)
    return " | ".join([p for p in compact if p])


def format_item_line(
    qtd: str | None,
    group_name: str | None,
    canonical_product: str | None,
    type: str | None,
    thickness: str | None,
    measure: str | None,
    length: str | None,
    *,
    is_manual_type: bool = False,
) -> str:
    item = format_item(
        group_name,
        canonical_product,
        type,
        thickness,
        measure,
        length,
        is_manual_type=is_manual_type,
    )
    if not item:
        return ""
    qty = _clean(qtd)
    return f"{qty} | {item}" if qty else item
