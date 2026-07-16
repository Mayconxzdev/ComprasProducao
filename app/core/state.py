from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from .bootstrap_runtime import RuntimeBootstrapResult, ensure_runtime_bootstrap
from .catalog_db import CatalogDB
from .config import AppConfig
from .config_sync import sync_from_master
from .history_store import HistoryStore
from .models import QuoteItem
from .product_memory import ProductMemory
from .procurement_workflow import ProcurementWorkflowStore
from .search_index import SupplierIndex
from .supplier_meta_store_nas import SupplierMetaStoreNAS


@dataclass
class AppState:
    config: AppConfig = field(default_factory=AppConfig.load)
    index: SupplierIndex = field(default_factory=SupplierIndex)
    last_load_errors: List[str] = field(default_factory=list)
    last_load_warnings: List[str] = field(default_factory=list)
    last_loaded_files: List[str] = field(default_factory=list)
    last_loaded_count: int = 0

    product_query: str = ""
    items: List[QuoteItem] = field(default_factory=list)
    observations: str = ""
    selected_emails: Set[str] = field(default_factory=set)

    memory: ProductMemory = field(default_factory=ProductMemory)
    history: HistoryStore | None = None
    catalog: CatalogDB | None = None
    supplier_meta: SupplierMetaStoreNAS | None = None
    workflow_store: ProcurementWorkflowStore | None = None

    startup_sync_ok: bool = False
    startup_sync_message: str = ""
    startup_bootstrap_ok: bool = False
    startup_bootstrap_message: str = ""
    startup_bootstrap_warnings: List[str] = field(default_factory=list)

    def __post_init__(self):
        try:
            self.startup_sync_ok, self.startup_sync_message = sync_from_master(self.config)
        except Exception:
            self.startup_sync_ok = False
            self.startup_sync_message = "Falha ao sincronizar configuracao master."
        try:
            boot: RuntimeBootstrapResult = ensure_runtime_bootstrap(self.config, force_refresh=False)
            self.startup_bootstrap_ok = bool(boot.ok)
            self.startup_bootstrap_message = str(boot.message or "")
            self.startup_bootstrap_warnings = list(boot.warnings or [])
        except Exception:
            self.startup_bootstrap_ok = False
            self.startup_bootstrap_message = "Falha ao preparar bootstrap local."
            self.startup_bootstrap_warnings = []

        try:
            if self.history is None:
                self.history = HistoryStore(self.config)
            else:
                self.history.rebind_config(self.config)
        except Exception:
            pass

        try:
            if self.catalog is None:
                self.catalog = CatalogDB(self.config)
                self.catalog.init_schema()
        except Exception:
            pass

        try:
            if self.supplier_meta is None:
                self.supplier_meta = SupplierMetaStoreNAS(self.config)
            else:
                self.supplier_meta.rebind_config(self.config)
        except Exception:
            pass

        try:
            if self.workflow_store is None:
                self.workflow_store = ProcurementWorkflowStore()
        except Exception:
            pass

        try:
            self.memory.load()
        except Exception as e:
            print(f"Error loading memory: {e}")

    def reset_quote(self) -> None:
        self.product_query = ""
        self.items.clear()
        self.observations = ""
        self.selected_emails.clear()
