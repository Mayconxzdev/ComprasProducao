from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Optional

from app.core.bootstrap_runtime import ensure_runtime_bootstrap
from app.core.perf_metrics import record_timing

from .ports import IndexBuilder, IndexCacheRepo, NasPathResolver


@dataclass
class ReloadBaseResult:
    ok: bool
    status_message: str
    suppliers_count: int = 0
    loaded_from_cache: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cache_note: str = ""


class ReloadBaseUseCase:
    def __init__(
        self,
        *,
        app_state: Any,
        nas_path_resolver: NasPathResolver,
        index_builder: IndexBuilder,
        cache_repo: IndexCacheRepo,
    ):
        self.app_state = app_state
        self.nas_path_resolver = nas_path_resolver
        self.index_builder = index_builder
        self.cache_repo = cache_repo

    def _resolve_sources(self, force_refresh: bool) -> tuple[list[str], str]:
        config = self.app_state.config
        nas_path = config.nas_master_path
        if nas_path:
            resolved = self.nas_path_resolver.resolve(nas_path, force_refresh=force_refresh)
            if resolved.xlsx_path:
                return [resolved.xlsx_path], resolved.message
            fallback = list(config.xlsx_sources or [])
            return fallback, f"Cache NAS falhou: {resolved.message}" if fallback else resolved.message
        return list(config.xlsx_sources or []), "Usando caminhos diretos (sem NAS)"

    def execute(
        self,
        *,
        force_refresh: bool = False,
        on_catalog_progress: Optional[Callable[[str], None]] = None,
    ) -> ReloadBaseResult:
        t0 = time.perf_counter()
        try:
            ensure_runtime_bootstrap(self.app_state.config, force_refresh=force_refresh)
        except Exception:
            pass
        sources, status_note = self._resolve_sources(force_refresh)
        if not sources:
            out = ReloadBaseResult(ok=False, status_message="Nenhuma fonte de dados configurada", cache_note=status_note)
            record_timing("core.reload_base_ms", (time.perf_counter() - t0) * 1000.0)
            return out

        config = self.app_state.config
        config.xlsx_sources = list(sources)

        sig = self.cache_repo.compute_signature(sources, config.xlsx_sheet_name)
        cached = None if force_refresh else self.cache_repo.load(sig)
        if cached is not None:
            idx, res = cached
            self.app_state.index = idx
            self.app_state.last_load_errors = list(res.errors)
            self.app_state.last_load_warnings = list(res.warnings)
            self.app_state.last_loaded_files = list(res.loaded_files)
            self.app_state.last_loaded_count = int(res.suppliers_count)
            self._touch_catalog(force_refresh, on_catalog_progress)
            out = ReloadBaseResult(
                ok=(len(res.errors) == 0),
                status_message=status_note,
                suppliers_count=int(res.suppliers_count),
                loaded_from_cache=True,
                warnings=list(res.warnings),
                errors=list(res.errors),
                cache_note="indice local aquecido",
            )
            record_timing("core.reload_base_ms", (time.perf_counter() - t0) * 1000.0)
            return out

        idx, res = self.index_builder.build(sources, sheet_name=config.xlsx_sheet_name)
        self.app_state.index = idx
        self.app_state.last_load_errors = list(res.errors)
        self.app_state.last_load_warnings = list(res.warnings)
        self.app_state.last_loaded_files = list(res.loaded_files)
        self.app_state.last_loaded_count = int(res.suppliers_count)
        self._touch_catalog(force_refresh, on_catalog_progress)
        try:
            self.cache_repo.save(sig, idx, res)
        except Exception:
            pass

        out = ReloadBaseResult(
            ok=(len(res.errors) == 0),
            status_message=status_note,
            suppliers_count=int(res.suppliers_count),
            loaded_from_cache=False,
            warnings=list(res.warnings),
            errors=list(res.errors),
        )
        record_timing("core.reload_base_ms", (time.perf_counter() - t0) * 1000.0)
        return out

    def _touch_catalog(self, force_refresh: bool, on_catalog_progress: Optional[Callable[[str], None]]) -> None:
        catalog = getattr(self.app_state, "catalog", None)
        if not catalog:
            return
        try:
            catalog.rebuild_if_needed(on_progress=on_catalog_progress, force=force_refresh)
        except Exception:
            pass


class ReindexCatalogUseCase:
    def __init__(self, *, app_state: Any):
        self.app_state = app_state

    def execute(self, *, force: bool = True, on_progress: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        catalog = getattr(self.app_state, "catalog", None)
        if catalog is None:
            return False, "catalogo indisponivel"
        return catalog.rebuild_if_needed(force=force, on_progress=on_progress)
