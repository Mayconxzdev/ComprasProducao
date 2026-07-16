from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable

from app.application.context import AppContext
from app.core.local_supplier_db import (
    add_local_supplier,
    connect_local_db,
    get_supplier_ui_overrides,
    list_pending_master_sync,
    mark_pending_master_sync_failure,
    mark_pending_master_sync_success,
    queue_pending_master_sync,
    save_supplier_override,
    save_supplier_ui_override,
    update_local_supplier,
)
from app.core.supplier_meta_store_nas import supplier_key_from_obj
from app.core.supplier_scoring import compute_supplier_score
from app.core.utils_text import normalize_text
from app.core.xlsx_master_writer import upsert_supplier_in_master

from app.qt.models.supplier_table_model import SupplierRow


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _first_non_empty(values: Iterable[str]) -> str:
    for value in values:
        text = _clean(value)
        if text:
            return text
    return ""


class SupplierEditService:
    def __init__(self, app_context: AppContext) -> None:
        self.app_context = app_context

    def flush_pending_master_sync(self, *, max_items: int = 20) -> None:
        self._flush_pending_master_sync(max_items=max_items)

    def build_rows(self, suppliers: list[object], selected_emails: set[str]) -> list[SupplierRow]:
        overrides = self._load_ui_overrides()
        history_rows: list[dict] = []
        state = getattr(self.app_context, "state", None)
        history = getattr(state, "history", None)
        if (
            history is not None
            and getattr(state, "config", None) is not None
            and state.config.is_feature_enabled("supplier_operational_score", True)
        ):
            try:
                history_rows = list(history.get_global_history(""))
            except Exception:
                history_rows = []
        rows: list[SupplierRow] = []

        for supplier in suppliers:
            supplier_id = _clean(getattr(supplier, "supplier_id", ""))
            email = _clean(getattr(supplier, "email", ""))
            email_norm = normalize_text(email)
            supplier_key = supplier_id or f"EMAIL:{email_norm}"
            meta_key = supplier_key_from_obj(supplier)
            is_local = supplier_id.startswith("LOCAL:")

            company = _clean(getattr(supplier, "empresa", ""))
            contact = _first_non_empty((getattr(supplier, "contato_nome", ""), getattr(supplier, "contato", "")))
            phone = _clean(getattr(supplier, "telefone", ""))
            products = tuple(self._extract_supplier_products(supplier))

            override = overrides.get(supplier_key) or {}
            company = _clean(override.get("empresa_override")) or company
            contact = _clean(override.get("contato_override")) if "contato_override" in override else contact
            phone = _clean(override.get("telefone_override")) if "telefone_override" in override else phone
            email = _clean(override.get("email_override")) or email
            override_products = _clean(override.get("produtos_override"))
            if override_products:
                products = tuple(self.parse_products_text(override_products))

            meta_store = getattr(state, "supplier_meta", None)
            meta = None
            if meta_store is not None:
                try:
                    meta = meta_store.get(meta_key)
                except Exception:
                    meta = None
            meta_status = str(getattr(meta, "status", "ATIVO") or "ATIVO").upper()
            meta_notes = str(getattr(meta, "notes", "") or "")

            score_value = 0
            score_reason = ""
            score_breakdown: tuple[tuple[str, int], ...] = ()
            try:
                score = compute_supplier_score(
                    supplier_key=supplier_key,
                    item_context={"product_query": _clean(getattr(state, "product_query", ""))},
                    events=history_rows,
                    supplier=supplier,
                    meta=meta,
                )
                score_value = int(score.total)
                score_reason = score.reasons[0] if score.reasons else ""
                score_breakdown = tuple(score.breakdown.items())
            except Exception:
                score_value = 0

            rows.append(
                SupplierRow(
                    supplier_key=supplier_key,
                    supplier_id=supplier_id,
                    is_local=is_local,
                    company=company or "?",
                    contact=contact,
                    phone=phone,
                    email=email,
                    products=products,
                    operational_score=score_value,
                    score_reason=score_reason,
                    score_breakdown=score_breakdown,
                    selected=bool(email and normalize_text(email) in selected_emails),
                    raw_supplier=supplier,
                    meta_key=meta_key,
                    meta_status=meta_status,
                    meta_notes=meta_notes,
                )
            )
        return rows

    def parse_products_text(self, text: str) -> list[str]:
        raw = _clean(text)
        if not raw:
            return []
        chunks = raw.replace("|", ",").replace(";", ",").split(",")
        products: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            value = _clean(chunk)
            if not value:
                continue
            norm = normalize_text(value)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            products.append(value)
        return products

    def _extract_supplier_products(self, supplier: object) -> list[str]:
        items = getattr(supplier, "items", None)
        if isinstance(items, list) and items:
            out: list[str] = []
            seen: set[str] = set()
            for item in items:
                name = _clean(getattr(item, "item", "")) or _clean(str(item))
                if not name:
                    continue
                key = normalize_text(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(name)
            if out:
                return out

        product_text = _first_non_empty((getattr(supplier, "material_produto", ""), getattr(supplier, "produto", "")))
        if not product_text:
            return []
        return self.parse_products_text(product_text)

    def _load_ui_overrides(self) -> dict[str, dict[str, str]]:
        try:
            conn = connect_local_db()
            try:
                return get_supplier_ui_overrides(conn)
            finally:
                conn.close()
        except Exception:
            return {}

    def _validate_email(self, email: str) -> bool:
        return bool(_EMAIL_RE.match(email or ""))

    def _normalize_phone(self, phone: str) -> str:
        text = _clean(phone)
        if not text:
            return ""
        text = re.sub(r"[^\d()+\-\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def apply_edit(self, row: SupplierRow, field: str, typed: str) -> tuple[bool, SupplierRow, str]:
        value = _clean(typed)

        if field == "company":
            if not value:
                return False, row, "Empresa não pode ficar vazia."
            next_row = replace(row, company=value)
        elif field == "contact":
            next_row = replace(row, contact=value)
        elif field == "phone":
            next_row = replace(row, phone=self._normalize_phone(value))
        elif field == "email":
            if not value:
                return False, row, "E-mail e obrigatorio."
            if not self._validate_email(value):
                return False, row, "E-mail invalido."
            next_row = replace(row, email=value)
        elif field == "products":
            products = tuple(self.parse_products_text(value))
            if not products:
                return False, row, "Informe ao menos um produto."
            next_row = replace(row, products=products)
        else:
            return False, row, "Campo não editável."

        ok, message = self._persist_row(row, next_row)
        if not ok:
            return False, row, message
        return True, next_row, "ok"

    def _persist_row(self, previous: SupplierRow, current: SupplierRow) -> tuple[bool, str]:
        try:
            conn = connect_local_db()
            try:
                if current.is_local:
                    local_id = self._local_id_from_key(current.supplier_id or current.supplier_key)
                    if local_id is not None:
                        update_local_supplier(
                            conn,
                            local_supplier_id=local_id,
                            empresa=current.company,
                            contato=current.contact,
                            email=current.email,
                            telefone=current.phone,
                        )
                elif current.supplier_id:
                    # Keep compatibility with master sources through local overrides.
                    save_supplier_override(
                        conn,
                        supplier_id=current.supplier_id,
                        email_override=current.email,
                        contato_override=current.contact,
                        telefone_override=current.phone,
                    )

                save_supplier_ui_override(
                    conn,
                    supplier_key=current.supplier_key,
                    empresa_override=current.company,
                    contato_override=current.contact,
                    email_override=current.email,
                    telefone_override=current.phone,
                    produtos_override=current.products_edit_text,
                )
            finally:
                conn.close()
        except Exception as exc:
            return False, f"Falha ao salvar alteracao: {exc}"

        # Keep selected email in sync after edits.
        old_email = previous.email_norm
        new_email = current.email_norm
        if old_email and old_email in self.app_context.state.selected_emails and old_email != new_email:
            self.app_context.state.selected_emails.discard(old_email)
        if current.selected and new_email:
            self.app_context.state.selected_emails.add(new_email)

        # Best-effort NAS sync with local queue fallback.
        self._sync_master_best_effort(current)
        self._flush_pending_master_sync()
        return True, "ok"

    def _master_sync_key(self, row: SupplierRow) -> str:
        if row.supplier_id:
            return row.supplier_id
        if row.email_norm:
            return f"EMAIL:{row.email_norm}"
        return row.supplier_key

    def _master_payload_for_row(self, row: SupplierRow) -> dict:
        items = []
        for product in row.products:
            product_text = _clean(product)
            if not product_text:
                continue
            items.append({"item_id": product_text})
        return {
            "empresa": row.company,
            "contato": row.contact,
            "telefone": row.phone,
            "email": row.email,
            "cidade": "",
            "uf": "",
            "endereco": "",
            "obs": "",
            "items": items,
        }

    def _sync_master_best_effort(self, row: SupplierRow) -> None:
        payload = self._master_payload_for_row(row)
        if not _clean(payload.get("empresa")) or not _clean(payload.get("email")):
            return
        try:
            ok, message, _supplier_id = upsert_supplier_in_master(self.app_context.state.config, payload)
        except Exception as exc:
            ok, message = False, str(exc)
        if ok:
            return
        try:
            conn = connect_local_db()
            try:
                queue_pending_master_sync(
                    conn,
                    sync_key=self._master_sync_key(row),
                    payload=payload,
                    error_message=message,
                )
            finally:
                conn.close()
        except Exception:
            pass

    def _flush_pending_master_sync(self, *, max_items: int = 20) -> None:
        try:
            conn = connect_local_db()
            try:
                pending = list_pending_master_sync(conn, limit=max_items)
                if not pending:
                    return
                for entry in pending:
                    payload = entry.get("payload")
                    if not isinstance(payload, dict):
                        mark_pending_master_sync_failure(
                            conn,
                            row_id=int(entry.get("id") or 0),
                            error_message="payload_invalido",
                        )
                        continue
                    ok, message, _supplier_id = upsert_supplier_in_master(self.app_context.state.config, payload)
                    row_id = int(entry.get("id") or 0)
                    if ok:
                        mark_pending_master_sync_success(conn, row_id=row_id)
                    else:
                        mark_pending_master_sync_failure(conn, row_id=row_id, error_message=message)
            finally:
                conn.close()
        except Exception:
            pass

    def _local_id_from_key(self, key: str) -> int | None:
        text = _clean(key)
        if not text:
            return None
        if text.startswith("LOCAL:"):
            text = text.split(":", 1)[1]
        return int(text) if text.isdigit() else None

    def set_company_override_for_supplier(self, supplier: object, company: str) -> tuple[bool, str]:
        supplier_id = _clean(getattr(supplier, "supplier_id", ""))
        email = _clean(getattr(supplier, "email", ""))
        supplier_key = supplier_id or f"EMAIL:{normalize_text(email)}"
        if not supplier_key:
            return False, "Fornecedor sem chave para override."
        if not _clean(company):
            return False, "Nome de empresa invalido."
        try:
            conn = connect_local_db()
            try:
                save_supplier_ui_override(
                    conn,
                    supplier_key=supplier_key,
                    empresa_override=_clean(company),
                )
            finally:
                conn.close()
        except Exception as exc:
            return False, f"Falha ao salvar merge: {exc}"
        return True, "ok"

    def create_local_supplier(
        self,
        *,
        company: str,
        email: str,
        contact: str = "",
        phone: str = "",
        products_text: str = "",
    ) -> tuple[bool, str, SupplierRow | None]:
        company = _clean(company)
        email = _clean(email)
        if not company:
            return False, "Empresa e obrigatoria.", None
        if not self._validate_email(email):
            return False, "E-mail invalido.", None
        try:
            conn = connect_local_db()
            try:
                local_supplier_id = add_local_supplier(
                    conn,
                    empresa=company,
                    contato=_clean(contact),
                    email=email,
                    telefone=self._normalize_phone(phone),
                )
                supplier_key = f"LOCAL:{local_supplier_id}"
                if _clean(products_text):
                    save_supplier_ui_override(
                        conn,
                        supplier_key=supplier_key,
                        empresa_override=company,
                        contato_override=_clean(contact),
                        email_override=email,
                        telefone_override=self._normalize_phone(phone),
                        produtos_override=", ".join(self.parse_products_text(products_text)),
                    )
            finally:
                conn.close()
        except Exception as exc:
            return False, f"Falha ao criar fornecedor: {exc}", None

        # Best effort: try to register new supplier in master XLSX.
        row_for_sync = SupplierRow(
            supplier_key=supplier_key,
            supplier_id=supplier_key,
            is_local=True,
            company=company,
            contact=_clean(contact),
            phone=self._normalize_phone(phone),
            email=email,
            products=tuple(self.parse_products_text(products_text)),
            selected=False,
            raw_supplier=None,
        )
        self._sync_master_best_effort(row_for_sync)
        self._flush_pending_master_sync()

        return True, "ok", row_for_sync
