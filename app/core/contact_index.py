from __future__ import annotations

from dataclasses import dataclass

from app.core.state import AppState
from app.core.utils_text import normalize_text


def _clean(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ContactSuggestion:
    label: str
    value: str
    email: str
    company: str
    contact_name: str
    source: str
    recipient_text: str


def _build_label(company: str, contact_name: str, email: str) -> str:
    clean_company = _clean(company)
    clean_contact = _clean(contact_name)
    clean_email = _clean(email)
    if clean_contact:
        return f"{clean_contact} <{clean_email}>"
    if clean_company:
        return f"{clean_company} <{clean_email}>"
    return clean_email


def _build_search_value(company: str, contact_name: str, email: str) -> str:
    return " | ".join(part for part in (_clean(contact_name), _clean(company), _clean(email)) if part)


def _build_recipient_text(company: str, contact_name: str, email: str) -> str:
    clean_company = _clean(company)
    clean_contact = _clean(contact_name)
    clean_email = _clean(email)
    if clean_company and clean_contact:
        return f"{clean_company} | {clean_contact} | {clean_email}"
    if clean_contact:
        return f"{clean_contact} | {clean_email}"
    if clean_company:
        return f"{clean_company} | {clean_email}"
    return clean_email


def _to_contact(
    *,
    email: str,
    company: str,
    contact_name: str,
    source: str,
) -> ContactSuggestion | None:
    clean_email = _clean(email)
    if "@" not in clean_email:
        return None
    clean_company = _clean(company)
    clean_contact = _clean(contact_name)
    label = _build_label(clean_company, clean_contact, clean_email)
    return ContactSuggestion(
        label=label,
        value=_build_search_value(clean_company, clean_contact, clean_email),
        email=clean_email,
        company=clean_company,
        contact_name=clean_contact,
        source=source,
        recipient_text=_build_recipient_text(clean_company, clean_contact, clean_email),
    )


def _from_suppliers(app_state: AppState) -> list[ContactSuggestion]:
    rows: list[ContactSuggestion] = []
    index = getattr(app_state, "index", None)
    suppliers: list[object] = []
    if index is not None and hasattr(index, "get_all_suppliers"):
        try:
            suppliers = list(index.get_all_suppliers() or [])
        except Exception:
            suppliers = []
    if not suppliers:
        suppliers = list(getattr(index, "suppliers", []) or [])
    for supplier in suppliers:
        row = _to_contact(
            email=getattr(supplier, "email", ""),
            company=getattr(supplier, "empresa", ""),
            contact_name=getattr(supplier, "contato_nome", "") or getattr(supplier, "contato", ""),
            source="supplier",
        )
        if row is not None:
            rows.append(row)
    return rows


def _from_history(app_state: AppState) -> list[ContactSuggestion]:
    rows: list[ContactSuggestion] = []
    history = getattr(app_state, "history", None)
    if history is None:
        return rows
    try:
        events = history.get_global_history("")
    except Exception:
        return rows

    for event in events:
        recipients = event.get("recipients") or []
        if not isinstance(recipients, list):
            continue
        for recipient in recipients:
            if not isinstance(recipient, dict):
                continue
            row = _to_contact(
                email=recipient.get("email", ""),
                company=recipient.get("empresa", ""),
                contact_name=recipient.get("contato_nome") or recipient.get("contato"),
                source="history",
            )
            if row is not None:
                rows.append(row)
    return rows


def _from_thunderbird(thunderbird_contacts: list[dict] | None) -> list[ContactSuggestion]:
    rows: list[ContactSuggestion] = []
    for contact in thunderbird_contacts or []:
        if not isinstance(contact, dict):
            continue
        row = _to_contact(
            email=contact.get("email", ""),
            company=contact.get("company", ""),
            contact_name=contact.get("name", ""),
            source="thunderbird",
        )
        if row is not None:
            rows.append(row)
    return rows


def build_contact_index(app_state: AppState, thunderbird_contacts: list[dict] | None) -> list[ContactSuggestion]:
    ordered_rows: list[ContactSuggestion] = [
        *_from_suppliers(app_state),
        *_from_thunderbird(thunderbird_contacts),
        *_from_history(app_state),
    ]

    deduped: dict[str, ContactSuggestion] = {}
    for row in ordered_rows:
        key = normalize_text(row.email)
        if not key or key in deduped:
            continue
        deduped[key] = row

    return sorted(
        deduped.values(),
        key=lambda row: (
            0 if row.contact_name else 1,
            0 if row.company else 1,
            normalize_text(row.contact_name),
            normalize_text(row.company),
            normalize_text(row.email),
        ),
    )
