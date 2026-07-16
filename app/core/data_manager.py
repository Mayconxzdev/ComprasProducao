from __future__ import annotations
import time
from dataclasses import dataclass
from typing import List, Tuple, Union
from .xlsx_loader import load_suppliers_from_xlsx
from .xlsx_loader_pro import detect_schema, load_professional_xlsx
from .search_index import SupplierIndex
from .search_index_pro import ProSearchIndex
from .app_log import setup_logging

logger = setup_logging()

@dataclass
class LoadResult:
    suppliers_count: int
    warnings: List[str]
    errors: List[str]
    loaded_files: List[str]
    finished_at: float
    schema_type: str  # 'simple' or 'professional'

def build_index(xlsx_sources: List[str], sheet_name: str = "Fornecedores") -> Tuple[Union[SupplierIndex, ProSearchIndex], LoadResult]:
    """
    Auto-detect schema and build appropriate index

    Returns: (index, load_result)
    """
    if not xlsx_sources:
        return SupplierIndex(), LoadResult(0, [], ["Nenhum arquivo XLSX configurado"], [], time.time(), 'simple')

    # Auto-detect from first file
    first_file = xlsx_sources[0]
    schema = detect_schema(first_file)

    if schema == 'professional':
        return _build_professional_index(xlsx_sources)
    else:
        return _build_simple_index(xlsx_sources, sheet_name)

def _build_simple_index(xlsx_sources: List[str], sheet_name: str) -> Tuple[SupplierIndex, LoadResult]:
    """Build simple index (legacy single-sheet mode)"""
    idx = SupplierIndex()
    warnings: List[str] = []
    errors: List[str] = []
    loaded: List[str] = []

    for path in xlsx_sources:
        try:
            suppliers, w = load_suppliers_from_xlsx(path, sheet_name=sheet_name)
            idx.add_many(suppliers)
            warnings.extend(w)
            loaded.append(path)
            logger.info(f"Carregado: {path} | linhas válidas: {len(suppliers)}")
        except Exception as e:
            msg = f"Falha ao carregar {path}: {e}"
            errors.append(msg)
            logger.error(msg)

    for w in warnings:
        logger.info(w)

    res = LoadResult(
        suppliers_count=len(idx.suppliers),
        warnings=warnings,
        errors=errors,
        loaded_files=loaded,
        finished_at=time.time(),
        schema_type='simple'
    )
    return idx, res

def _build_professional_index(xlsx_sources: List[str]) -> Tuple[ProSearchIndex, LoadResult]:
    """Build professional index (multi-sheet mode) with local supplier merger"""
    from .data_merger import merge_data

    warnings: List[str] = []
    errors: List[str] = []
    loaded: List[str] = []

    # Professional schema typically has all data in first file
    path = xlsx_sources[0]

    try:
        logger.info(f"Loading professional XLSX: {path}")
        dataset = load_professional_xlsx(path)

        # Merge with local suppliers and overrides
        logger.info("Merging with local suppliers...")
        merged_dataset = merge_data(dataset)

        # Build index
        idx = ProSearchIndex()
        idx.build_from_dataset(merged_dataset)

        loaded.append(path)
        logger.info(f"Professional index built: {idx.supplier_count} suppliers, {idx.item_count} items")

    except Exception as e:
        msg = f"Falha ao carregar {path}: {e}"
        errors.append(msg)
        logger.error(msg, exc_info=True)
        # Return empty index
        idx = ProSearchIndex()

    res = LoadResult(
        suppliers_count=idx.supplier_count,
        warnings=warnings,
        errors=errors,
        loaded_files=loaded,
        finished_at=time.time(),
        schema_type='professional'
    )
    return idx, res
