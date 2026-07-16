from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.application.ports import XlsxResolveResult
from app.core.cache_manager import get_xlsx_path
from app.core.data_manager import build_index
from app.core.index_cache import compute_signature, load_index_cache, save_index_cache


@dataclass
class CoreCatalogRepo:
    catalog: Any

    def query_suppliers(
        self,
        query: str,
        *,
        category: str = "",
        limit: int = 100,
        offset: int = 0,
        broad_mode: bool = False,
    ) -> list[Any]:
        return self.catalog.query_suppliers(
            query,
            category=category,
            limit=limit,
            offset=offset,
            broad_mode=broad_mode,
        )

    def rebuild_if_needed(self, *, on_progress=None, force: bool = False) -> tuple[bool, str]:
        return self.catalog.rebuild_if_needed(on_progress=on_progress, force=force)


class CoreIndexBuilder:
    def build(self, xlsx_sources: list[str], *, sheet_name: str = "Fornecedores") -> tuple[Any, Any]:
        return build_index(xlsx_sources, sheet_name=sheet_name)


class CoreIndexCacheRepo:
    def compute_signature(self, xlsx_sources: list[str], sheet_name: str) -> str:
        return compute_signature(xlsx_sources, sheet_name)

    def load(self, signature: str) -> Optional[tuple[Any, Any]]:
        return load_index_cache(signature)

    def save(self, signature: str, index: Any, load_result: Any) -> None:
        save_index_cache(signature, index, load_result)


class CoreNasPathResolver:
    def resolve(self, nas_master_path: str, *, force_refresh: bool = False) -> XlsxResolveResult:
        xlsx_path, message = get_xlsx_path(nas_master_path, force_refresh=force_refresh)
        return XlsxResolveResult(xlsx_path=xlsx_path, message=message)
