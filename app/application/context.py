from __future__ import annotations

from dataclasses import dataclass

from app.core.state import AppState
from app.infrastructure.adapters import CoreIndexBuilder, CoreIndexCacheRepo, CoreNasPathResolver

from .job_manager import JobManager
from .use_cases import ReloadBaseUseCase, ReindexCatalogUseCase


@dataclass
class AppContext:
    state: AppState
    job_manager: JobManager
    reload_base_uc: ReloadBaseUseCase
    reindex_catalog_uc: ReindexCatalogUseCase

    @classmethod
    def bootstrap(cls) -> "AppContext":
        state = AppState()
        jobs = JobManager(max_workers=4)
        return cls(
            state=state,
            job_manager=jobs,
            reload_base_uc=ReloadBaseUseCase(
                app_state=state,
                nas_path_resolver=CoreNasPathResolver(),
                index_builder=CoreIndexBuilder(),
                cache_repo=CoreIndexCacheRepo(),
            ),
            reindex_catalog_uc=ReindexCatalogUseCase(app_state=state),
        )
