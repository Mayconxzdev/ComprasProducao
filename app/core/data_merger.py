"""
Data Merger: Combines master XLSX + local suppliers + overrides
"""
from __future__ import annotations
import logging
from typing import Dict, Set
from copy import deepcopy

from .models_pro import SupplierPro, ProDataSet
from .local_supplier_db import (
    connect_local_db, get_all_local_suppliers, get_local_supplier_items,
    get_all_overrides, SupplierOverride
)
from .utils_text import token_set

logger = logging.getLogger(__name__)


def _email_key(email: object) -> str:
    """Chave canônica para deduplicação de e-mail.

    E-mail é case-insensitive no uso prático do catálogo; normalizar com
    strip/casefold evita duplicatas como Contato@... e contato@... sem
    transformar o endereço em tokens de busca.
    """
    return str(email or "").strip().casefold()


def merge_data(master_dataset: ProDataSet) -> ProDataSet:
    """
    Merge master XLSX data with local suppliers and overrides

    Process:
    1. Load master suppliers
    2. Apply overrides from supplier_overrides
    3. Add local suppliers
    4. Deduplicate by email (prefer master+override over local)

    Returns: ProDataSet with merged suppliers
    """
    logger.info("Merging master data with local database...")

    try:
        conn = connect_local_db()
    except Exception as e:
        logger.warning(f"Could not connect to local.db: {e}. Using master data only.")
        return master_dataset

    try:
        # Step 1: Copy master suppliers
        merged_suppliers: Dict[str, SupplierPro] = deepcopy(master_dataset.suppliers)

        # Step 2: Apply overrides
        overrides = get_all_overrides(conn)
        for supplier_id, override in overrides.items():
            if supplier_id in merged_suppliers:
                apply_override(merged_suppliers[supplier_id], override)
                logger.debug(f"Applied override to {supplier_id}")

        # Step 3: Add local suppliers
        local_suppliers = get_all_local_suppliers(conn)
        local_count = 0

        for local_sup in local_suppliers:
            # Create virtual supplier_id
            virtual_id = f"LOCAL:{local_sup.local_supplier_id}"

            # Get items for this local supplier
            local_items = get_local_supplier_items(conn, local_sup.local_supplier_id)

            # Convert to Item objects
            items_list = []
            for link in local_items:
                # Get item details from master
                if link.item_id in master_dataset.items:
                    master_item = master_dataset.items[link.item_id]
                    items_list.append(master_item)

            # Build tokens
            tokens = token_set(f"{local_sup.empresa} {local_sup.contato or ''} {local_sup.email} {local_sup.cidade or ''} {local_sup.uf or ''}")

            # Create SupplierPro
            supplier_pro = SupplierPro(
                supplier_id=virtual_id,
                empresa=local_sup.empresa,
                contato=local_sup.contato or "",
                email=local_sup.email,
                telefone=local_sup.telefone or "",
                endereco=local_sup.endereco or "",
                cidade=local_sup.cidade or "",
                uf=local_sup.uf or "",
                obs=local_sup.obs or "",
                items=items_list,
                serv_corte=any(link.serv_corte for link in local_items),
                serv_dobra=any(link.serv_dobra for link in local_items),
                serv_entrega=any(link.serv_entrega for link in local_items),
                serv_retira=any(link.serv_retira for link in local_items),
                tokens=tokens,
                is_valid=True,  # Local suppliers are assumed valid
                invalid_reason="",
                source_file="LOCAL",
                is_local=True
            )

            merged_suppliers[virtual_id] = supplier_pro
            local_count += 1

        logger.info(f"Merged: {len(master_dataset.suppliers)} master + {local_count} local = {len(merged_suppliers)} total")

        # Step 4: Deduplicate by email
        merged_suppliers = deduplicate_by_email(merged_suppliers)

        # Create merged dataset
        merged_dataset = ProDataSet(
            suppliers=merged_suppliers,
            items=master_dataset.items,  # Items unchanged
            links=master_dataset.links,   # Links unchanged
            synonyms=master_dataset.synonyms  # Synonyms unchanged
        )

        conn.close()
        return merged_dataset

    except Exception as e:
        logger.error(f"Error during merge: {e}", exc_info=True)
        conn.close()
        # Fallback to master data if merge fails
        return master_dataset


def apply_override(supplier: SupplierPro, override: SupplierOverride):
    """Apply override values to supplier (mutates supplier)"""
    if override.email_override:
        supplier.email = override.email_override
        # Re-validate
        supplier.is_valid = bool(supplier.email and '@' in supplier.email)
        if supplier.is_valid:
            supplier.invalid_reason = ""
        else:
            supplier.invalid_reason = "EMAIL inválido"

    if override.contato_override:
        supplier.contato = override.contato_override

    if override.telefone_override:
        supplier.telefone = override.telefone_override

    if override.endereco_override:
        supplier.endereco = override.endereco_override

    if override.cidade_override:
        supplier.cidade = override.cidade_override

    if override.uf_override:
        supplier.uf = override.uf_override

    if override.obs_override:
        supplier.obs = override.obs_override

    # Rebuild tokens with updated data
    supplier.tokens = token_set(
        f"{supplier.empresa} {supplier.contato} {supplier.email} "
        f"{supplier.cidade} {supplier.uf}"
    )


def deduplicate_by_email(suppliers: Dict[str, SupplierPro]) -> Dict[str, SupplierPro]:
    """Deduplicate suppliers by email.

    Priority: master (with overrides) > local.
    Duplicates are summarized in normal logs and detailed only in DEBUG so the
    Windows terminal does not spam the buyer during normal use.
    """
    seen_emails: Dict[str, str] = {}  # email -> supplier_id
    to_remove: Set[str] = set()
    master_duplicates: list[tuple[str, str, str]] = []
    local_duplicates: list[tuple[str, str, str]] = []

    # First pass: collect master suppliers
    for supplier_id, supplier in suppliers.items():
        if not supplier.email:
            continue

        email_norm = _email_key(supplier.email)
        if not email_norm:
            continue

        if not supplier_id.startswith("LOCAL:"):
            # Master supplier (with or without override)
            if email_norm in seen_emails:
                master_duplicates.append((str(supplier.email), supplier_id, seen_emails[email_norm]))
            else:
                seen_emails[email_norm] = supplier_id

    # Second pass: check local suppliers against masters
    for supplier_id, supplier in suppliers.items():
        if not supplier_id.startswith("LOCAL:"):
            continue

        if not supplier.email:
            continue

        email_norm = _email_key(supplier.email)
        if not email_norm:
            continue

        if email_norm in seen_emails:
            # Conflict: local has same email as master
            local_duplicates.append((str(supplier.email), supplier_id, seen_emails[email_norm]))
            to_remove.add(supplier_id)
        else:
            seen_emails[email_norm] = supplier_id

    for email, current_id, kept_id in master_duplicates:
        logger.debug("Duplicate email in master: %s (%s and %s)", email, current_id, kept_id)
    for email, local_id, kept_id in local_duplicates:
        logger.debug("Duplicate email: %s. Keeping master %s, removing local %s", email, kept_id, local_id)

    # Remove duplicates
    for supplier_id in to_remove:
        del suppliers[supplier_id]

    if master_duplicates or local_duplicates:
        logger.info(
            "Supplier email dedupe: %s master duplicate(s), %s local duplicate(s) consolidated.",
            len(master_duplicates),
            len(local_duplicates),
        )

    return suppliers
