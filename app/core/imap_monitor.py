from __future__ import annotations

import email
import hashlib
import imaplib
import json
import logging
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from email import policy
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig, IMAPProfile, ensure_app_data_dir
from .dashboard_insights import parse_ts, recipients
from .dpapi_crypto import decrypt_password, encrypt_password, is_available as dpapi_available
from .history_store import HistoryStore
from .smtp_handler import get_password_from_profile

logger = logging.getLogger(__name__)
STATE_FILE = "imap_reply_sync_state.json"
UID_RESCAN_OVERLAP = 40
REF_RE = re.compile(r"\bCV-\d{4}-[0-9A-Fa-f]{6,}\b")


@dataclass(frozen=True)
class SyncSummary:
    checked_accounts: int = 0
    scanned_messages: int = 0
    matched_replies: int = 0
    new_replies: int = 0
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def message(self) -> str:
        base = f"{self.new_replies} resposta(s) nova(s), {self.matched_replies} relacionada(s), {self.scanned_messages} e-mail(s) verificados."
        if self.errors:
            return base + " Avisos: " + " | ".join(self.errors[:3])
        return base


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm_email(value: object) -> str:
    return _clean(value).lower()


def _state_path() -> Path:
    return ensure_app_data_dir() / STATE_FILE


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data: dict[str, Any]) -> None:
    try:
        _state_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass




def _parse_email_message(data: bytes) -> Message:
    """Parse RFC822 data with an explicit modern policy.

    BytesParser(policy=policy.default) returns EmailMessage where possible,
    enabling get_body()/iter_attachments() and recording defects instead of
    silently failing on real-world malformed supplier e-mails.
    """
    return BytesParser(policy=policy.default).parsebytes(data)

def _extract_uidvalidity(select_data: object) -> int:
    """Extract UIDVALIDITY from IMAP SELECT response when the server exposes it."""
    try:
        parts = select_data if isinstance(select_data, (list, tuple)) else [select_data]
        blob = " ".join(
            (item.decode("utf-8", errors="ignore") if isinstance(item, (bytes, bytearray)) else str(item or ""))
            for item in parts
        )
        match = re.search(r"UIDVALIDITY\s+(\d+)", blob, flags=re.I)
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


def _decode_mime(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        return str(make_header(decode_header(text))).strip()
    except Exception:
        return text


def _strip_reply_prefix(subject: str) -> str:
    text = _decode_mime(subject).strip()
    for _ in range(8):
        new = re.sub(r"^\s*(re|res|fw|fwd)\s*[:\-]\s*", "", text, flags=re.I).strip()
        if new == text:
            break
        text = new
    return " ".join(text.casefold().split())


def _text_from_message(msg: Message, max_chars: int = 12000) -> str:
    """Return the main readable body of an e-mail reply.

    Prefer EmailMessage.get_body() when available, then fall back to the
    compatibility walk() path. Attachments are never mixed into the body.
    """
    try:
        if hasattr(msg, "get_body"):
            body_part = msg.get_body(preferencelist=("plain", "html"))  # type: ignore[attr-defined]
            if body_part is not None:
                text = body_part.get_content()  # type: ignore[attr-defined]
                if body_part.get_content_type() == "text/html":  # type: ignore[attr-defined]
                    text = re.sub(r"<br\s*/?>", "\n", str(text), flags=re.I)
                    text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\r", "", str(text))
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
                return text[:max_chars]
    except Exception:
        pass

    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if content_type == "text/html":
                    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
                    text = re.sub(r"<[^>]+>", " ", text)
                parts.append(text)
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload is not None:
                charset = msg.get_content_charset() or "utf-8"
                parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass
    text = "\n".join(parts)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text[:max_chars]


def _safe_filename(value: object, fallback: str) -> str:
    name = _decode_mime(value) or fallback
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip().strip(".")
    return name[:160] or fallback


def _attachments_from_message(msg: Message, event_id: str) -> list[dict[str, str | int]]:
    """Persist reply attachments to local cache and return metadata for the UI."""
    out: list[dict[str, str | int]] = []
    base = ensure_app_data_dir() / "reply_attachments" / _safe_filename(event_id, "reply")
    counter = 0
    try:
        iterator = msg.iter_attachments() if hasattr(msg, "iter_attachments") else [p for p in msg.walk() if "attachment" in str(p.get("Content-Disposition") or "").lower()]
    except Exception:
        iterator = []
    for part in iterator:
        try:
            payload = part.get_payload(decode=True) if hasattr(part, "get_payload") else None
            if payload is None and hasattr(part, "get_content"):
                content = part.get_content()
                payload = content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8", errors="ignore")
            if not payload:
                continue
            counter += 1
            filename = _safe_filename(part.get_filename() if hasattr(part, "get_filename") else "", f"anexo_{counter}.bin")
            base.mkdir(parents=True, exist_ok=True)
            target = base / filename
            if target.exists():
                stem, suffix = target.stem, target.suffix
                target = base / f"{stem}_{counter}{suffix}"
            target.write_bytes(bytes(payload))
            out.append({
                "filename": target.name,
                "path": str(target),
                "content_type": part.get_content_type() if hasattr(part, "get_content_type") else "application/octet-stream",
                "size_bytes": len(payload),
            })
        except Exception:
            continue
    return out

def _message_date(msg: Message) -> str:
    raw = _clean(msg.get("Date"))
    if raw:
        try:
            dt = email.utils.parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        except Exception:
            pass
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _email_addr(value: object) -> str:
    try:
        return email.utils.parseaddr(_clean(value))[1].strip().lower()
    except Exception:
        return ""


def _sender_name(value: object) -> str:
    try:
        return _decode_mime(email.utils.parseaddr(_clean(value))[0])
    except Exception:
        return ""


def _reply_ref_from_message(subject: str, body: str, msg_obj: Message | None = None) -> str:
    if msg_obj is not None:
        for header_name in ("In-Reply-To", "References"):
            val = _clean(msg_obj.get(header_name))
            if val:
                m = REF_RE.search(val)
                if m:
                    return m.group(0).upper()
    for text in (subject, body):
        m = REF_RE.search(text or "")
        if m:
            return m.group(0).upper()
    return ""


def build_rfq_ref(event_id: str) -> str:
    raw = _clean(event_id)
    if not raw:
        raw = hashlib.sha1(str(time.time()).encode()).hexdigest()[:12]
    return f"CV-{datetime.now().year}-{raw[:8].upper()}"


def _event_rfq_id(row: dict[str, Any]) -> str:
    extra = row.get("extra") or {}
    if isinstance(extra, dict):
        for key in ("rfq_id", "tracking_ref"):
            val = _clean(extra.get(key)).upper()
            if val:
                return val
    return build_rfq_ref(_clean(row.get("event_id")))


def outgoing_events(history_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in history_rows:
        if not isinstance(row, dict):
            continue
        et = _clean(row.get("event_type")).casefold()
        if et in {"imap_response", "manual_response", "followup_sent"}:
            continue
        if recipients(row):
            out.append(row)
    return out


def response_events(history_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in history_rows:
        if not isinstance(row, dict):
            continue
        if _clean(row.get("event_type")).casefold() in {"imap_response", "manual_response"}:
            out.append(row)
    return out


def responses_for_event(source_event: dict[str, Any], all_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    event_id = _clean(source_event.get("event_id"))
    rfq_id = _event_rfq_id(source_event)
    out: list[dict[str, Any]] = []
    for row in response_events(all_rows):
        extra = row.get("extra") or {}
        if not isinstance(extra, dict):
            continue
        if _clean(extra.get("source_event_id")) == event_id or _clean(extra.get("rfq_id")).upper() == rfq_id:
            out.append(row)
    out.sort(key=lambda r: _clean(r.get("ts")), reverse=True)
    return out


def response_summary_for_event(source_event: dict[str, Any], all_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    recs = recipients(source_event)
    replies = responses_for_event(source_event, all_rows)
    replied: dict[str, dict[str, Any]] = {}
    for row in replies:
        extra = row.get("extra") or {}
        if not isinstance(extra, dict):
            continue
        sender = _norm_email(extra.get("sender") or (row.get("recipients") or [{}])[0].get("email"))
        if sender and sender not in replied:
            replied[sender] = row
    pending = []
    answered = []
    for rec in recs:
        email_addr = _norm_email(rec.get("email"))
        if email_addr and email_addr in replied:
            answered.append(rec)
        else:
            pending.append(rec)
    return {
        "total": len(recs),
        "answered_count": len(answered),
        "pending_count": len(pending),
        "answered": answered,
        "pending": pending,
        "replies": replies,
    }


def _match_reply_to_event(*, sender: str, subject: str, body: str, date_iso: str, events: list[dict[str, Any]], msg_obj: Message | None = None) -> tuple[dict[str, Any] | None, str]:
    ref = _reply_ref_from_message(subject, body, msg_obj)
    if ref:
        for event in events:
            if _event_rfq_id(event).upper() == ref:
                return event, "referencia"
    subject_norm = _strip_reply_prefix(subject)
    sender_norm = _norm_email(sender)
    date_dt = parse_ts(date_iso)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        rec_emails = {_norm_email(r.get("email")) for r in recipients(event)}
        if sender_norm not in rec_emails:
            continue
        event_dt = parse_ts(event.get("ts"))
        if date_dt is not None and event_dt is not None and date_dt < event_dt:
            continue
        score = 20
        ev_subject = _strip_reply_prefix(_clean(event.get("subject")))
        if subject_norm and ev_subject and (subject_norm in ev_subject or ev_subject in subject_norm):
            score += 50
        product = _clean(event.get("product_query")).casefold()
        if product and product in subject_norm:
            score += 15
        candidates.append((score, event))
    if not candidates:
        return None, "sem_correspondencia"
    candidates.sort(key=lambda item: (item[0], _clean(item[1].get("ts"))), reverse=True)
    return candidates[0][1], "remetente_assunto"


def get_imap_password(config: AppConfig, profile: IMAPProfile) -> str:
    if profile.password_protected_b64 and dpapi_available():
        try:
            return decrypt_password(profile.password_protected_b64) or ""
        except Exception:
            pass
    # Fallback: most deployments use the same mailbox/password for SMTP and IMAP.
    for smtp in (config.smtp_profiles or {}).values():
        if _norm_email(getattr(smtp, "username", "")) == _norm_email(profile.username):
            return get_password_from_profile(smtp, allow_prompt=False) or ""
    return ""


def set_imap_password(profile: IMAPProfile, password: str) -> None:
    text = _clean(password)
    if not text:
        profile.password_protected_b64 = ""
        profile.shared_password_b64 = ""
        return
    try:
        profile.password_protected_b64 = encrypt_password(text)
        profile.shared_password_b64 = ""
    except Exception as exc:
        profile.shared_password_b64 = ""
        raise RuntimeError("DPAPI indisponível; configure uma proteção local suportada.") from exc


def _fetch_recent_messages(account_key: str, profile: IMAPProfile, password: str, days_back: int, max_messages: int, events: list[dict[str, Any]]) -> tuple[list[tuple[str, bytes]], str]:
    mailbox = _clean(profile.mailbox) or "INBOX"
    host = _clean(profile.host) or "imap.example.com"
    port = int(profile.port or 993)
    state = _load_state()
    account_state = state.get(account_key) if isinstance(state.get(account_key), dict) else {}
    last_uid = int(account_state.get("last_uid") or 0)
    context = ssl.create_default_context()
    messages: list[tuple[str, bytes]] = []
    latest_uid = last_uid

    # Preparar critérios de pré-filtragem
    active_subjects = set()
    active_recipients = set()
    for ev in events:
        sub = _strip_reply_prefix(_clean(ev.get("subject")))
        if sub:
            active_subjects.add(sub)
        for r in recipients(ev):
            email_addr = _norm_email(r.get("email"))
            if email_addr:
                active_recipients.add(email_addr)

    with imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=int(profile.timeout_sec or 20)) as client:
        client.login(profile.username, password)
        typ, select_data = client.select(mailbox, readonly=True)
        if typ != "OK":
            return [], f"Não foi possível abrir {mailbox}."
        uidvalidity = _extract_uidvalidity(select_data)
        previous_uidvalidity = int(account_state.get("uidvalidity") or 0)
        if uidvalidity and previous_uidvalidity and uidvalidity != previous_uidvalidity:
            # RFC/IMAP: UID só é estável dentro da mesma UIDVALIDITY. Ao mudar, reinicia.
            last_uid = 0
            latest_uid = 0
        if uidvalidity:
            account_state["uidvalidity"] = uidvalidity
        try:
            cap_typ, cap_data = client.capability()
            if cap_typ == "OK":
                account_state["capabilities"] = " ".join(
                    item.decode("utf-8", errors="ignore") if isinstance(item, (bytes, bytearray)) else str(item)
                    for item in (cap_data or [])
                )[:500]
        except Exception:
            pass

        # Busca incremental por UID. Quando já existe last_uid, pedir ao servidor
        # apenas a janela nova (+ pequena sobreposição) é bem mais rápido do que
        # listar todos os e-mails dos últimos dias e filtrar localmente.
        scan_floor = max(0, last_uid - UID_RESCAN_OVERLAP) if last_uid else 0
        if scan_floor:
            typ, data = client.uid("search", None, f"UID {scan_floor + 1}:*")
        else:
            since_dt = datetime.now() - timedelta(days=max(1, days_back))
            since = since_dt.strftime("%d-%b-%Y")
            typ, data = client.uid("search", None, f'(SINCE "{since}")')
        if typ != "OK" or not data:
            return [], "Busca IMAP sem resultado."
        uids = [u.decode("ascii", errors="ignore") if isinstance(u, bytes) else str(u) for u in data[0].split()]
        numeric_uids = []
        # Revarre uma pequena janela de UIDs anteriores. Isso corrige casos em que
        # o primeiro scan ocorreu antes da cotação ficar registrada localmente,
        # sem duplicar respostas porque o histórico já ignora event_id repetido.
        for uid in uids:
            try:
                n = int(uid)
            except Exception:
                continue
            if n > scan_floor:
                numeric_uids.append(n)
        numeric_uids = numeric_uids[-max_messages:]

        for uid_num in numeric_uids:
            uid = str(uid_num)
            # 1. Fetch apenas dos cabeçalhos essenciais (PEEK para não alterar status de lido)
            typ, header_data = client.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])")
            if typ != "OK" or not header_data:
                continue
            raw_header = b""
            for part in header_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw_header = bytes(part[1])
                    break
            if not raw_header:
                continue
            try:
                hdr_msg = _parse_email_message(raw_header)
            except Exception:
                continue
            subject = _decode_mime(hdr_msg.get("Subject"))
            sender = _email_addr(hdr_msg.get("From"))

            # Verificar se é candidato a resposta
            is_candidate = False

            # Critério 1: In-Reply-To ou References contém tracking ID
            for header_name in ("In-Reply-To", "References"):
                val = _clean(hdr_msg.get(header_name))
                if val and REF_RE.search(val):
                    is_candidate = True
                    break
            # Critério 2: Corpo ou assunto contém tracking ID
            if not is_candidate:
                ref = _reply_ref_from_message(subject, "", hdr_msg)
                if ref:
                    is_candidate = True
            # Critério 3: O remetente está entre nossos destinatários das cotações ativas
            if not is_candidate and sender in active_recipients:
                subject_norm = _strip_reply_prefix(subject)
                for active_sub in active_subjects:
                    if subject_norm and (subject_norm in active_sub or active_sub in subject_norm):
                        is_candidate = True
                        break

            if not is_candidate:
                latest_uid = max(latest_uid, uid_num)
                continue

            # 2. É um candidato! Baixar o e-mail completo
            typ, msg_data = client.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    messages.append((uid, bytes(part[1])))
                    latest_uid = max(latest_uid, uid_num)
                    break
    account_state["last_uid"] = latest_uid
    account_state["last_sync_at"] = datetime.now().isoformat(timespec="seconds")
    account_state["mailbox"] = mailbox
    state[account_key] = account_state
    _save_state(state)
    return messages, "ok"


def sync_inbox_replies(
    config: AppConfig,
    history: HistoryStore | None,
    *,
    days_back: int = 21,
    max_messages_per_account: int = 120,
) -> SyncSummary:
    if history is None:
        return SyncSummary(errors=("Histórico indisponível.",))
    try:
        all_rows = history.get_global_history("")
    except Exception as exc:
        return SyncSummary(errors=(f"Histórico indisponível: {exc}",))
    events = outgoing_events(all_rows)
    checked = scanned = matched = created = 0
    errors: list[str] = []
    config.ensure_imap_profiles()
    for account_key, profile in (config.imap_profiles or {}).items():
        if not getattr(profile, "enabled", True):
            continue
        if not _clean(profile.username):
            continue
        checked += 1
        password = get_imap_password(config, profile)
        if not password:
            errors.append(f"Senha IMAP não configurada para {profile.label or profile.username}.")
            continue
        try:
            raw_messages, msg = _fetch_recent_messages(account_key, profile, password, days_back, max_messages_per_account, events)
        except Exception as exc:
            msg_str = str(exc)
            if "login failed" in msg_str.lower() or "authentication failed" in msg_str.lower() or "credentials" in msg_str.lower():
                errors.append(f"Não foi possível conectar ao e-mail {profile.username}. Verifique a senha nas configurações.")
            elif "timeout" in msg_str.lower() or "timed out" in msg_str.lower():
                errors.append(f"Tempo limite esgotado ao conectar ao e-mail {profile.username}. Verifique a conexão de rede.")
            elif "ssl" in msg_str.lower() or "certificate" in msg_str.lower():
                errors.append(f"Erro de segurança SSL/TLS com o e-mail {profile.username}.")
            else:
                errors.append(f"Não foi possível conectar ao e-mail {profile.username}. Verifique as configurações.")
            continue
        if msg != "ok" and not raw_messages:
            pass
        for uid, raw in raw_messages:
            scanned += 1
            try:
                msg_obj = _parse_email_message(raw)
            except Exception:
                continue
            subject = _decode_mime(msg_obj.get("Subject"))
            sender = _email_addr(msg_obj.get("From"))
            sender_display = _sender_name(msg_obj.get("From")) or sender
            body = _text_from_message(msg_obj)
            date_iso = _message_date(msg_obj)
            source_event, matched_by = _match_reply_to_event(sender=sender, subject=subject, body=body, date_iso=date_iso, events=events, msg_obj=msg_obj)
            if source_event is None:
                continue
            matched += 1
            extra = source_event.get("extra") if isinstance(source_event.get("extra"), dict) else {}
            rfq_id = _event_rfq_id(source_event)
            message_id = _clean(msg_obj.get("Message-ID")) or f"uid:{uid}"
            event_id = hashlib.sha256(f"imap|{profile.username}|{uid}|{message_id}".encode("utf-8", errors="ignore")).hexdigest()[:24]
            attachments = _attachments_from_message(msg_obj, event_id)
            reply_event = {
                "event_id": event_id,
                "ts": date_iso,
                "event_type": "imap_response",
                "status": "responded",
                "product_query": _clean(source_event.get("product_query")),
                "subject": subject,
                "body": body[:12000],
                "recipients": [{"empresa": sender_display, "email": sender}],
                "items": [],
                "user": "IMAP",
                "pc_name": "Servidor de e-mail",
                "failed_emails": [],
                "extra": {
                    "source_event_id": _clean(source_event.get("event_id")),
                    "rfq_id": rfq_id,
                    "sender": sender,
                    "sender_name": sender_display,
                    "to_account": profile.username,
                    "message_uid": uid,
                    "message_id": message_id,
                    "matched_by": matched_by,
                    "source_subject": _clean(source_event.get("subject")),
                    "company_key": _clean(extra.get("company_key")),
                    "attachments": attachments,
                    "has_attachments": bool(attachments),
                },
            }
            ok, append_msg = history.append_event(reply_event)
            if ok and append_msg != "duplicado_ignorado":
                created += 1
    return SyncSummary(checked, scanned, matched, created, tuple(errors))


def register_manual_response(
    history: HistoryStore | None,
    source_event: dict[str, Any],
    *,
    supplier_name: str,
    supplier_email: str,
    response_text: str,
) -> tuple[bool, str]:
    if history is None:
        return False, "Histórico indisponível."
    source_event_id = _clean(source_event.get("event_id"))
    if not source_event_id:
        return False, "Cotação inválida."
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    rfq_id = _event_rfq_id(source_event)
    event_id = hashlib.sha256(f"manual|{source_event_id}|{supplier_email}|{now}".encode("utf-8", errors="ignore")).hexdigest()[:24]
    row = {
        "event_id": event_id,
        "ts": now,
        "event_type": "manual_response",
        "status": "responded",
        "product_query": _clean(source_event.get("product_query")),
        "subject": f"Resposta registrada — {_clean(source_event.get('subject'))}",
        "body": _clean(response_text),
        "recipients": [{"empresa": _clean(supplier_name) or supplier_email, "email": supplier_email}],
        "items": [],
        "user": "manual",
        "pc_name": "app",
        "failed_emails": [],
        "extra": {
            "source_event_id": source_event_id,
            "rfq_id": rfq_id,
            "sender": supplier_email,
            "sender_name": _clean(supplier_name),
            "matched_by": "manual",
        },
    }
    ok, msg = history.append_event(row)
    return ok, msg
