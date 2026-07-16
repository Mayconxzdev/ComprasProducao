from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any

from .response_analyzer import looks_like_valid_quote


def _clean(value: object) -> str:
    return str(value or "").strip()


def parse_ts(value: object) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "").replace("/", "-"))
    except Exception:
        return None


def human_datetime(value: object) -> str:
    dt = parse_ts(value)
    if dt is None:
        return _clean(value) or "-"
    today = date.today()
    if dt.date() == today:
        return f"Hoje, {dt:%H:%M}"
    if (today - dt.date()).days == 1:
        return f"Ontem, {dt:%H:%M}"
    return dt.strftime("%d/%m/%Y, %H:%M")


def short_text(text: object, max_len: int = 46) -> str:
    value = " ".join(_clean(text).split())
    if len(value) <= max_len:
        return value or "Sem descrição"
    return value[: max_len - 1].rstrip() + "…"


def event_title(row: dict[str, Any]) -> str:
    subject = _clean(row.get("subject"))
    product = _clean(row.get("product_query"))
    extra = row.get("extra") or {}
    if isinstance(extra, dict):
        kind = _clean(extra.get("request_type") or extra.get("kind"))
        oc = _clean(extra.get("oc_number") or extra.get("purchase_order_number"))
        if oc:
            return f"OC {oc}"
        if kind in {"cotacao_frete", "freight"} and product:
            return f"Frete — {short_text(product, 34)}"
    if product.upper().startswith("OC "):
        return short_text(product, 44)
    if subject:
        # Assuntos antigos vinham como "VENT RIO <> ORDEM DE COMPRA N° TESTE";
        # para a lista operacional, mostrar a ação, não o prefixo corporativo.
        cleaned = re.sub(r"\s*<>\s*", " — ", subject).strip()
        cleaned = re.sub(r"^(VESPER|VENT\s*RIO)\s*[—:-]\s*", "", cleaned, flags=re.I)
        oc_match = re.search(r"ORDEM\s+DE\s+COMPRA\s*(?:N[º°O.]*)?\s*[-: ]*\s*(.+)", cleaned, flags=re.I)
        if oc_match:
            return short_text("OC " + oc_match.group(1).strip(), 44)
        cleaned = cleaned.replace("COTAÇÃO DE FRETE", "Frete").replace("COTACAO DE FRETE", "Frete")
        cleaned = cleaned.replace("COTAÇÃO", "Cotação").replace("COTACAO", "Cotação")
        return short_text(cleaned, 52)
    return short_text(product, 52)


def event_type_label(row: dict[str, Any]) -> str:
    subject = _clean(row.get("subject")).casefold()
    event_type = _clean(row.get("event_type")).casefold()
    product = _clean(row.get("product_query")).casefold()
    extra = row.get("extra") or {}
    kind = _clean(extra.get("request_type") if isinstance(extra, dict) else "").casefold()
    text = " ".join([subject, event_type, product, kind])
    if "ordem" in text or "purchase" in text or " oc" in f" {text}":
        return "Ordem de compra"
    if "frete" in text or "transport" in text:
        return "Cotação de frete"
    if "painel" in text or " ex" in f" {text}":
        return "Painéis EX"
    return "Cotação de material"


def company_label(row: dict[str, Any]) -> str:
    extra = row.get("extra") or {}
    if isinstance(extra, dict):
        company_key = _clean(extra.get("company_key")).casefold()
        if company_key == "ventrio":
            return "Vent Rio"
        if company_key == "vesper":
            return "Vesper"
        sender = _clean(extra.get("sender") or extra.get("from_email") or extra.get("from"))
    else:
        sender = ""
    if "ventrio" in sender.casefold():
        return "Vent Rio"
    if "vesper" in sender.casefold():
        return "Vesper"
    user = _clean(row.get("user"))
    return user or "-"


def recipients(row: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    raw = row.get("recipients") or []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        email = _clean(item.get("email"))
        empresa = _clean(item.get("empresa")) or email
        if not email and not empresa:
            continue
        out.append({"empresa": empresa, "email": email, "contato_nome": _clean(item.get("contato_nome"))})
    return out


def _email_key(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        from email.utils import parseaddr
        parsed = parseaddr(text)[1] or text
    except Exception:
        parsed = text
    return parsed.strip().casefold()


def is_archived(row: dict[str, Any]) -> bool:
    extra = row.get("extra") or {}
    if isinstance(extra, dict):
        if bool(extra.get("is_archived") or extra.get("archived")):
            return True
        if _clean(extra.get("archived_at")):
            return True
    return _clean(row.get("status")).casefold() in {"archived", "arquivado", "quote_archived"}


def _event_rfq_id(row: dict[str, Any]) -> str:
    extra = row.get("extra") or {}
    if isinstance(extra, dict):
        for key in ("rfq_id", "tracking_ref"):
            val = _clean(extra.get(key)).upper()
            if val:
                return val
    event_id = _clean(row.get("event_id"))
    return f"CV-{date.today().year}-{event_id[:8].upper()}" if event_id else ""


def response_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if _clean(r.get("event_type")).casefold() in {"imap_response", "manual_response"}]


def outgoing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked = {"imap_response", "manual_response", "followup_sent"}
    return [r for r in rows if _clean(r.get("event_type")).casefold() not in blocked and not is_technical(r)]


def responses_for_event(row: dict[str, Any], all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    event_id = _clean(row.get("event_id"))
    rfq_id = _event_rfq_id(row)
    out: list[dict[str, Any]] = []
    for resp in response_events(all_rows):
        extra = resp.get("extra") or {}
        if not isinstance(extra, dict):
            continue
        if _clean(extra.get("source_event_id")) == event_id or (rfq_id and _clean(extra.get("rfq_id")).upper() == rfq_id):
            out.append(resp)
    out.sort(key=lambda r: _clean(r.get("ts")), reverse=True)
    return out


def response_summary(row: dict[str, Any], all_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # visible_history_rows() enriches each outgoing row with the full response
    # summary built from all events. Re-use it in the UI; otherwise a detail panel
    # fed only with outgoing rows would say "Respondido" on the card and "Pendente"
    # inside the details.
    extra_cached = row.get("extra") or {}
    if isinstance(extra_cached, dict) and isinstance(extra_cached.get("_response_summary"), dict):
        cached = dict(extra_cached.get("_response_summary") or {})
        cached.setdefault("answered_count", len(cached.get("answered") or []))
        cached.setdefault("pending_count", len(cached.get("pending") or []))
        cached.setdefault("total", len(recipients(row)))
        cached.setdefault("replies", [])
        cached.setdefault("valid_quote_count", sum(1 for r in cached.get("replies", []) if looks_like_valid_quote(r.get("body", "") if isinstance(r, dict) else "")))
        return cached

    all_rows = all_rows if all_rows is not None else []
    recs = recipients(row)
    replies = responses_for_event(row, all_rows)
    replied: dict[str, dict[str, Any]] = {}
    valid_quote_count = 0
    for resp in replies:
        extra = resp.get("extra") or {}
        sender = _email_key(extra.get("sender") if isinstance(extra, dict) else "")
        if not sender:
            raw_recs = recipients(resp)
            sender = _email_key(raw_recs[0].get("email") if raw_recs else "")
        if looks_like_valid_quote(resp.get("body", "")):
            valid_quote_count += 1
        if sender and sender not in replied:
            replied[sender] = resp
    answered = []
    pending = []
    for rec in recs:
        if _email_key(rec.get("email")) in replied:
            answered.append(rec)
        else:
            pending.append(rec)
    return {
        "answered_count": len(answered),
        "pending_count": len(pending),
        "total": len(recs),
        "answered": answered,
        "pending": pending,
        "replies": replies,
        "valid_quote_count": valid_quote_count,
    }

def status_group(row: dict[str, Any]) -> str:
    if is_archived(row):
        return "Arquivado"
    extra = row.get("extra") or {}
    status = _clean(row.get("status")).casefold()
    event_type = _clean(row.get("event_type")).casefold()
    if status in {"sent_smtp_fail", "thunderbird_fail"} or "fail" in status or "falha" in status:
        return "Falha"
    if "confirm" in status:
        return "Confirmado"
    if isinstance(extra, dict):
        summary = extra.get("_response_summary")
        if isinstance(summary, dict):
            total = int(summary.get("total") or 0)
            answered = int(summary.get("answered_count") or 0)
            pending = int(summary.get("pending_count") or 0)
            # Quando sabemos quantos destinatários existem, a resposta real deve
            # mandar no status visual. Isso evita 'Respondido' com 0/1 respondeu.
            if total:
                valid_quote_count = int(summary.get("valid_quote_count") or 0)
                if answered >= total:
                    return "Respondido" if valid_quote_count > 0 else "Sem cotação válida"
                if answered > 0 and pending > 0:
                    return "Parcial"
                return "Pendente"
    if "respond" in status or "respon" in status:
        return "Respondido"
    if status in {"sent_smtp_ok", "opened_thunderbird"} or "send" in event_type or "smtp" in event_type:
        return "Pendente"
    if status in {"generated", "quote_generated"} or event_type in {"rfq_generated", "workflow_transition"}:
        return "Pendente"
    return "Pendente"

def is_technical(row: dict[str, Any]) -> bool:
    return _clean(row.get("event_type")) in {"legacy_json", "legacy_sqlite", "workflow_transition"} or _clean(row.get("status")).startswith("workflow_")



def _passes_cutover(history: Any, row: dict[str, Any]) -> bool:
    """Hide legacy/test rows created before the operational IMAP cutover.

    The setting lives in AppConfig and is only applied to real HistoryStore
    instances. Test doubles or external callers without config keep the old
    behavior.
    """
    cfg = getattr(history, "config", None)
    if cfg is None or not bool(getattr(cfg, "hide_pre_tracking_history", False)):
        return True
    cutover = parse_ts(getattr(cfg, "history_tracking_cutover_at", ""))
    ts = parse_ts(row.get("ts"))
    if cutover is None or ts is None:
        return True
    return ts >= cutover

def visible_history_rows(history: Any, *, query: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
    if history is None:
        return []
    try:
        all_rows = [row for row in list(history.get_global_history("") or []) if isinstance(row, dict)]
    except Exception:
        return []
    rows = [row for row in outgoing_rows(all_rows) if _passes_cutover(history, row)]

    # Arquivamento agora é estado explícito da cotação, não corte fixo por data.
    if include_archived:
        rows = [r for r in rows if is_archived(r)]
    else:
        rows = [r for r in rows if not is_archived(r)]

    q = _clean(query).casefold()
    if q:
        def matches(row: dict[str, Any]) -> bool:
            hay = [
                _clean(row.get("product_query")),
                _clean(row.get("subject")),
                event_title(row),
                event_type_label(row),
                company_label(row),
            ]
            for rec in recipients(row):
                hay.append(_clean(rec.get("empresa")))
                hay.append(_clean(rec.get("email")))
            return q in " ".join(hay).casefold()
        rows = [row for row in rows if matches(row)]
    enriched: list[dict[str, Any]] = []
    for row in rows:
        clone = dict(row)
        extra = dict(clone.get("extra") or {}) if isinstance(clone.get("extra"), dict) else {}
        extra["_response_summary"] = response_summary(clone, all_rows)
        clone["extra"] = extra
        enriched.append(clone)
    enriched.sort(key=lambda r: _clean(r.get("ts")), reverse=True)
    return enriched


def dashboard_metrics(rows: list[dict[str, Any]]) -> dict[str, int]:
    today = date.today()
    today_rows = [row for row in rows if (parse_ts(row.get("ts")) or datetime.min).date() == today]
    base = today_rows if today_rows else rows[:50]
    sent = sum(1 for row in base if status_group(row) != "Falha")
    failed = sum(1 for row in base if status_group(row) == "Falha")
    pending = sum(1 for row in base if status_group(row) in {"Pendente", "Parcial"})
    answered = sum(1 for row in base if status_group(row) in {"Respondido", "Confirmado"})
    return {"sent": sent, "answered": answered, "pending": pending, "failed": failed, "total": len(rows)}


def supplier_count(index: Any) -> int:
    try:
        if hasattr(index, "get_all_suppliers"):
            return len(list(index.get_all_suppliers() or []))
        return len(list(getattr(index, "suppliers", []) or []))
    except Exception:
        return 0


def item_count(index: Any) -> int:
    values: set[str] = set()
    try:
        suppliers = list(index.get_all_suppliers() if hasattr(index, "get_all_suppliers") else getattr(index, "suppliers", []))
    except Exception:
        suppliers = []
    for supplier in suppliers:
        for attr in ("produtos", "products", "itens", "items"):
            raw = getattr(supplier, attr, None)
            if isinstance(raw, str):
                parts = [p.strip() for p in raw.replace(";", ",").split(",")]
            elif isinstance(raw, (list, tuple, set)):
                parts = [str(p).strip() for p in raw]
            else:
                parts = []
            for part in parts:
                if part:
                    values.add(part.casefold())
    return len(values)


def recent_rows(rows: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    return rows[:limit]


def pending_rows(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    pending = [row for row in rows if status_group(row) in {"Pendente", "Parcial", "Falha"}]
    return pending[:limit]
