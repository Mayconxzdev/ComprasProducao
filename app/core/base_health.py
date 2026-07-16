from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .supplier_meta_store_nas import SupplierMetaStoreNAS, supplier_key_from_obj
from .utils_text import normalize_text


@dataclass
class BaseHealthReport:
    missing_email: List[object]
    archived: List[object]
    possible_duplicates: List[tuple[object, object, str]]
    items_without_supplier: List[str]


def _domain(email: str) -> str:
    e = normalize_text(email)
    if "@" not in e:
        return ""
    return e.split("@", 1)[1]


def analyze_base(index, meta_store: SupplierMetaStoreNAS) -> BaseHealthReport:
    suppliers = index.get_all_suppliers() if hasattr(index, "get_all_suppliers") else index.suppliers
    metas = meta_store.list_all()

    missing_email = [s for s in suppliers if "@" not in str(getattr(s, "email", "") or "")]
    archived = []
    for s in suppliers:
        key = supplier_key_from_obj(s)
        if metas.get(key) and metas[key].status == "ARQUIVADO":
            archived.append(s)

    by_domain = {}
    for s in suppliers:
        d = _domain(str(getattr(s, "email", "") or ""))
        if not d:
            continue
        by_domain.setdefault(d, []).append(s)

    duplicates: List[tuple[object, object, str]] = []
    for d, rows in by_domain.items():
        if len(rows) < 2:
            continue
        for i in range(len(rows) - 1):
            a = rows[i]
            b = rows[i + 1]
            na = normalize_text(getattr(a, "empresa", "") or "")
            nb = normalize_text(getattr(b, "empresa", "") or "")
            if na[:8] == nb[:8] or na in nb or nb in na:
                duplicates.append((a, b, d))

    items_without_supplier: List[str] = []
    if hasattr(index, "_items") and isinstance(getattr(index, "_items"), dict):
        all_items = getattr(index, "_items")
        with_supplier = set()
        for s in suppliers:
            for it in getattr(s, "items", []) or []:
                iid = getattr(it, "item_id", "")
                if iid:
                    with_supplier.add(iid)
        for iid, it in all_items.items():
            if iid not in with_supplier:
                name = getattr(it, "item", iid)
                items_without_supplier.append(f"{iid} - {name}")

    return BaseHealthReport(
        missing_email=missing_email,
        archived=archived,
        possible_duplicates=duplicates,
        items_without_supplier=items_without_supplier,
    )


def export_health_to_csv(report: BaseHealthReport, csv_path: Path) -> Path:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tipo", "empresa", "email", "extra"])
        for s in report.missing_email:
            w.writerow(["sem_email", getattr(s, "empresa", ""), getattr(s, "email", ""), ""])
        for s in report.archived:
            w.writerow(["arquivado", getattr(s, "empresa", ""), getattr(s, "email", ""), ""])
        for a, b, d in report.possible_duplicates:
            w.writerow(["duplicado", getattr(a, "empresa", ""), getattr(a, "email", ""), f"dominio={d}"])
            w.writerow(["duplicado", getattr(b, "empresa", ""), getattr(b, "email", ""), f"dominio={d}"])
        for i in report.items_without_supplier:
            w.writerow(["item_sem_fornecedor", i, "", ""])
    return csv_path
