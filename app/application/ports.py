from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass
class XlsxResolveResult:
    xlsx_path: Optional[str]
    message: str


class CatalogRepo(Protocol):
    def query_suppliers(
        self,
        query: str,
        *,
        category: str = "",
        limit: int = 100,
        offset: int = 0,
        broad_mode: bool = False,
    ) -> list[Any]:
        ...

    def rebuild_if_needed(self, *, on_progress=None, force: bool = False) -> tuple[bool, str]:
        ...


class IndexBuilder(Protocol):
    def build(self, xlsx_sources: list[str], *, sheet_name: str = "Fornecedores") -> tuple[Any, Any]:
        ...


class IndexCacheRepo(Protocol):
    def compute_signature(self, xlsx_sources: list[str], sheet_name: str) -> str:
        ...

    def load(self, signature: str) -> Optional[tuple[Any, Any]]:
        ...

    def save(self, signature: str, index: Any, load_result: Any) -> None:
        ...


class NasPathResolver(Protocol):
    def resolve(self, nas_master_path: str, *, force_refresh: bool = False) -> XlsxResolveResult:
        ...
