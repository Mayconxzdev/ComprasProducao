from __future__ import annotations

import re

_MONEY_RE = re.compile(
    r"(?<![\w/])(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})*|\d+)[,.]\d{2}(?!\w)",
    re.I,
)
_DEADLINE_PATTERNS = (
    re.compile(r"(?:prazo|entrega|disponibilidade|prev(?:is[aã]o)?|lead\s*time)\D{0,35}(\d{1,3}\s*(?:dias?\s+úteis|dias?\s+uteis|dias?|d\.u\.?|semanas?|meses?))", re.I),
    re.compile(r"\b(pronta\s+entrega|imediat[oa]|em\s+estoque|dispon[ií]vel(?:\s+para\s+retirada)?|retirada\s+imediata)\b", re.I),
)
_PAYMENT_PATTERNS = (
    re.compile(r"(?:pagamento|condi[cç][aã]o|faturamento|forma\s+de\s+pagamento)\s*[:\-]?\s*([^\n\r;]{3,90})", re.I),
    re.compile(r"\b(\d{1,3}\s*(?:ddl|dd|dias)|\d{1,3}\s*/\s*\d{1,3}(?:\s*/\s*\d{1,3})?|[aà]\s+vista|pix|boleto|dep[oó]sito|transfer[eê]ncia|cart[aã]o|faturado)\b", re.I),
)
_INVALID_HINTS = (
    "vou verificar", "irei verificar", "vamos verificar", "em breve", "retorno em breve",
    "já verifico", "ja verifico", "aguarde", "ok, vou", "não trabalhamos", "nao trabalhamos",
    "não temos", "nao temos", "sem estoque",
)
_POSITIVE_HINTS = (
    "cotação", "cotacao", "orçamento", "orcamento", "proposta", "valor", "preço", "preco",
    "prazo", "pagamento", "boleto", "pix", "disponível", "disponivel", "estoque", "pronta entrega",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalize_space(text: str) -> str:
    return re.sub(r"[ \t]+", " ", _clean(text))


_QUOTED_REPLY_MARKERS = (
    re.compile(r"^\s*Em\s+\d{1,2}/\d{1,2}/\d{2,4}.*\bescreveu\s*:\s*$", re.I),
    re.compile(r"^\s*On\s+.+\bwrote\s*:\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*(?:Original Message|Mensagem original)\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*De\s*:\s*.+", re.I),
)


def split_supplier_reply(text: str) -> tuple[str, str]:
    """Return (supplier_answer, quoted_history).

    Supplier replies often include the complete original request below lines like
    ``Em 08/07/2026 ... escreveu:`` or quote-prefixed ``>`` lines.  The app
    should extract prices from the supplier answer, not from our own request
    (delivery address, opening hours, reference dates, signature etc.).
    """
    body = _clean(text).replace("\r", "")
    if not body:
        return "", ""
    lines = body.split("\n")
    cut_at: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.match(stripped) for pattern in _QUOTED_REPLY_MARKERS):
            # Avoid cutting real supplier content that starts with a simple "De:"
            # unless we already captured at least one meaningful line.
            if i > 0 or not stripped.casefold().startswith("de:"):
                cut_at = i
                break
        if stripped.startswith(">") and i > 0:
            cut_at = i
            break
    if cut_at is None:
        answer_lines = lines
        quoted_lines: list[str] = []
    else:
        answer_lines = lines[:cut_at]
        quoted_lines = lines[cut_at:]
    answer = "\n".join(answer_lines).strip()
    quoted = "\n".join(quoted_lines).strip()
    # Remove trailing quote remnants and excessive blank lines from the answer.
    answer = re.sub(r"\n{3,}", "\n\n", answer).strip()
    quoted = re.sub(r"\n{4,}", "\n\n\n", quoted).strip()
    return answer, quoted


def supplier_reply_text(text: str) -> str:
    answer, _quoted = split_supplier_reply(text)
    return answer or _clean(text)


def _money_values(text: str) -> list[str]:
    out: list[str] = []
    for match in _MONEY_RE.finditer(text or ""):
        raw = match.group(0).strip()
        # Evita confundir percentual/código como preço.
        before = (text or "")[max(0, match.start() - 4):match.start()]
        after = (text or "")[match.end():match.end() + 4]
        if "%" in after or "/" in after or "cod" in before.casefold():
            continue
        value = raw if raw.upper().startswith("R$") else f"R$ {raw}"
        if value not in out:
            out.append(value)
    return out


def _first_deadline(text: str) -> str:
    for pattern in _DEADLINE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return _normalize_space(match.group(1))
    return ""


def _looks_like_calendar_date(value: str) -> bool:
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*\d{2,4})?\s*", value or "")
    if not m:
        return False
    day = int(m.group(1))
    month = int(m.group(2))
    return 1 <= day <= 31 and 1 <= month <= 12


def _first_payment(text: str) -> str:
    for pattern in _PAYMENT_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = _normalize_space(match.group(1))[:90]
            if _looks_like_calendar_date(value):
                continue
            return value
    return ""


def looks_like_valid_quote(text: str) -> bool:
    body = supplier_reply_text(text)
    if not body:
        return False
    low = body.casefold()
    prices = _money_values(body)
    deadline = _first_deadline(body)
    payment = _first_payment(body)
    has_positive_context = any(h in low for h in _POSITIVE_HINTS)
    if any(hint in low for hint in _INVALID_HINTS) and not prices:
        return False
    if prices:
        return True
    # Prazo/pagamento isolado só vira cotação provável se houver contexto comercial.
    return bool(has_positive_context and (deadline or payment))


def _infer_item_label(line: str, price: str) -> str:
    price_index = line.casefold().find(price.casefold())
    prefix = line[:price_index].strip(" -–—:\t") if price_index > 0 else ""
    prefix = re.sub(r"^(valor|pre[cç]o|total|unit[aá]rio|unit|vlr\.?|r\$|esta|est[aá]|fica|sai|sair|por[eé]m|porem)\s*[:\-]?\s*", "", prefix, flags=re.I).strip()
    qty = re.search(r"\b(\d{1,5})\s*(?:unid(?:ades?)?|und|pe[cç]as?|pcs?)\b", line, flags=re.I)
    if qty:
        return f"{qty.group(1)} unidade(s)"
    if prefix and len(prefix) >= 4 and not re.fullmatch(r"(?:reais?|r\$|a|por|para|sair|sai|esta|est[aá])", prefix, flags=re.I):
        return prefix[:110]
    return "Preço informado pelo fornecedor"


def extract_commercial_table(text: str, *, max_rows: int = 12) -> list[dict[str, str]]:
    """Extract a simple item/price/deadline/payment table from a supplier reply.

    Heurística conservadora: mostra o e-mail inteiro ao comprador e preenche apenas
    campos prováveis para acelerar conferência manual, sem inventar valores.
    """
    body = supplier_reply_text(text)
    if not body:
        return []
    global_deadline = _first_deadline(body)
    global_payment = _first_payment(body)
    rows: list[dict[str, str]] = []
    lines = [_normalize_space(line) for line in re.split(r"[\r\n]+", body) if _normalize_space(line)]

    for line in lines:
        prices = _money_values(line)
        if not prices:
            continue
        first_price = prices[0]
        item = _infer_item_label(line, first_price)
        rows.append({
            "item": item[:110],
            "preco": ", ".join(prices[:3]),
            "prazo": _first_deadline(line) or global_deadline or "não informado",
            "pagamento": _first_payment(line) or global_payment or "não informado",
            "observacao": "Conferir com o texto completo do e-mail.",
        })
        if len(rows) >= max_rows:
            break

    if not rows and looks_like_valid_quote(body):
        rows.append({
            "item": "Resposta do fornecedor",
            "preco": ", ".join(_money_values(body)[:4]) or "não identificado",
            "prazo": global_deadline or "não informado",
            "pagamento": global_payment or "não informado",
            "observacao": "Dados detectados automaticamente; conferir no texto completo.",
        })
    return rows


def quote_quality_label(text: str) -> str:
    if not _clean(text):
        return "Sem corpo"
    if looks_like_valid_quote(text):
        return "Cotação válida provável"
    return "Sem cotação válida"
