from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.core.email_templates import clean_text, dedupe_emails, parse_freight_fields, summarize_subject

REQUEST_MATERIAL = "material"
REQUEST_FREIGHT = "freight"
REQUEST_PURCHASE_ORDER = "purchase_order"

EMAIL_ANY_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
OC_RE = re.compile(r"(?:\bOC\b|ORDEM\s+DE\s+COMPRA|ORDEM\s+COMPRA)\D{0,12}(\d{3,8})", re.I)
LOOSE_OC_RE = re.compile(r"\b(?:OC|O\.C\.)\s*[-_:ºN°#]*\s*(\d{3,8})\b", re.I)

FREIGHT_MARKERS = (
    "descrição do material",
    "descricao do material",
    "quantidade de volumes",
    "peso total",
    "valor da nota fiscal",
    "medidas",
    "frete",
)
EX_MARKERS = (
    " área classificada",
    "area classificada",
    " invólucro ex",
    " involucro ex",
    "painel elétrico ex",
    "painel eletrico ex",
    "/ex",
    " ex ",
    " ip66",
    "atex",
)

SAFE_ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".zip",
}
BLOCKED_ATTACHMENT_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".scr",
    ".js",
    ".jse",
    ".vbs",
    ".vbe",
    ".ps1",
    ".msi",
    ".jar",
    ".lnk",
    ".reg",
}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class SmartAnalysis:
    request_type: str = REQUEST_MATERIAL
    confidence: int = 0
    summary: str = "MATERIAL"
    emails: list[str] = field(default_factory=list)
    freight_fields: dict[str, str] = field(default_factory=dict)
    oc_number: str = ""
    supplier_guess: str = ""
    ex_required: bool = False
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


def extract_emails(text: str) -> list[str]:
    return dedupe_emails(EMAIL_ANY_RE.findall(clean_text(text)))


def strip_email_only_lines(text: str) -> str:
    """Remove lines that are only e-mails/separators, preserving useful request text."""
    kept: list[str] = []
    for line in clean_text(text).splitlines():
        raw = line.strip()
        if not raw:
            kept.append(line)
            continue
        without_emails = EMAIL_ANY_RE.sub("", raw)
        without_seps = re.sub(r"[\s,;/\-–—]+", "", without_emails)
        if not without_seps:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _normalized(text: str) -> str:
    value = clean_text(text).lower()
    value = value.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a").replace("é", "e")
    value = value.replace("ê", "e").replace("í", "i").replace("ó", "o").replace("ô", "o").replace("ú", "u")
    return value


def looks_like_ex(text: str) -> bool:
    low = f" {_normalized(text)} "
    return any(marker in low for marker in EX_MARKERS)


def parse_oc_number(text: str, attachment_paths: Iterable[str] = ()) -> str:
    haystacks = [clean_text(text)]
    for path in attachment_paths:
        p = Path(str(path))
        haystacks.append(p.stem)
        haystacks.append(p.name)
    for haystack in haystacks:
        for pattern in (OC_RE, LOOSE_OC_RE):
            match = pattern.search(haystack)
            if match:
                return match.group(1).strip()
    # Fallback for files like "5614 - ALFAPAR.pdf".
    for path in attachment_paths:
        stem = Path(str(path)).stem
        match = re.search(r"\b(\d{4,8})\b", stem)
        if match and ("oc" in _normalized(stem) or "ordem" in _normalized(stem)):
            return match.group(1)
    return ""


def guess_supplier_from_attachment(attachment_paths: Iterable[str]) -> str:
    for path in attachment_paths:
        stem = Path(str(path)).stem
        original = stem
        stem = re.sub(r"(?i)\b(?:oc|ordem\s+de\s+compra|ordem\s+compra)\b", " ", stem)
        stem = re.sub(r"\b\d{3,8}\b", " ", stem)
        # Prefer text after separators: "OC 5614 - ALFAPAR" -> ALFAPAR
        parts = [p.strip(" _-–—") for p in re.split(r"[-–—]", original) if p.strip(" _-–—")]
        if len(parts) >= 2:
            candidate = re.sub(r"\b\d{3,8}\b", "", parts[-1]).strip(" _-–—")
            if len(candidate) >= 2:
                return candidate.upper()
        candidate = re.sub(r"[_\-–—]+", " ", stem)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if len(candidate) >= 2:
            return candidate.upper()
    return ""


def infer_request_type(text: str, attachment_paths: Iterable[str] = ()) -> tuple[str, int, list[str]]:
    hints: list[str] = []
    clean = clean_text(text)
    low = _normalized(clean)
    paths = [str(p) for p in attachment_paths]

    if parse_oc_number(clean, paths) or any("oc" in _normalized(Path(p).stem) or "ordem" in _normalized(Path(p).stem) for p in paths):
        hints.append("Detectei número/arquivo de ordem de compra.")
        return REQUEST_PURCHASE_ORDER, 90, hints

    marker_count = sum(1 for marker in FREIGHT_MARKERS if marker in low)
    parsed = parse_freight_fields(clean)
    parsed_count = sum(1 for value in parsed.values() if clean_text(value))
    if marker_count >= 2 or parsed_count >= 2:
        hints.append("Detectei dados típicos de frete: volumes, peso, NF ou medidas.")
        return REQUEST_FREIGHT, 88, hints

    if clean or paths:
        hints.append("Vou montar uma cotação de material/produto.")
    return REQUEST_MATERIAL, 55 if clean else 0, hints


def analyze_smart_input(text: str, attachment_paths: Iterable[str] = ()) -> SmartAnalysis:
    paths = [str(p) for p in attachment_paths]
    text_clean = clean_text(text)
    request_type, confidence, hints = infer_request_type(text_clean, paths)
    freight_fields = parse_freight_fields(text_clean)
    oc = parse_oc_number(text_clean, paths)
    supplier_guess = guess_supplier_from_attachment(paths)
    content_without_email_lines = strip_email_only_lines(text_clean)
    summary_source = freight_fields.get("descricao") or supplier_guess or content_without_email_lines or text_clean
    summary = summarize_subject(summary_source)
    emails = extract_emails(text_clean)
    ex_required = looks_like_ex(text_clean)

    warnings: list[str] = []
    if request_type == REQUEST_FREIGHT:
        missing = [
            label
            for key, label in (
                ("descricao", "descrição"),
                ("volumes", "volumes"),
                ("peso", "peso"),
                ("valor_nf", "valor da NF"),
                ("medidas", "medidas"),
            )
            if not clean_text(freight_fields.get(key, ""))
        ]
        if missing:
            warnings.append("Frete com campos pendentes: " + ", ".join(missing) + ".")
    if request_type == REQUEST_PURCHASE_ORDER and not oc:
        warnings.append("Não detectei o número da OC.")

    return SmartAnalysis(
        request_type=request_type,
        confidence=confidence,
        summary=summary,
        emails=emails,
        freight_fields=freight_fields,
        oc_number=oc,
        supplier_guess=supplier_guess,
        ex_required=ex_required,
        warnings=warnings,
        hints=hints,
    )


def validate_attachment_path(path: str) -> tuple[bool, str]:
    p = Path(clean_text(path))
    if not p.exists() or not p.is_file():
        return False, "Arquivo não encontrado."
    suffix = p.suffix.lower()
    if suffix in BLOCKED_ATTACHMENT_EXTENSIONS:
        return False, f"Extensão bloqueada por segurança: {suffix}"
    if suffix not in SAFE_ATTACHMENT_EXTENSIONS:
        return False, f"Extensão não permitida para anexo: {suffix or 'sem extensão'}"
    try:
        if p.stat().st_size > MAX_ATTACHMENT_BYTES:
            return False, "Arquivo acima de 25 MB."
    except OSError:
        return False, "Não foi possível ler o tamanho do arquivo."
    return True, "ok"
