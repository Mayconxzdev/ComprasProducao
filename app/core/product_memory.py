"""
Product Memory - Shared Intelligence for Autocomplete.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from .config import AppConfig, ensure_app_data_dir
from .config_sync import get_master_dir

logger = logging.getLogger(__name__)

MEMORY_FILE = "product_memory.json"


class ProductMemory:
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._loaded = False
        self._local_cache_path = ensure_app_data_dir() / "cache" / MEMORY_FILE

    def get_server_path(self) -> str:
        cfg = AppConfig.load()
        master_dir = get_master_dir(cfg)
        if master_dir is None:
            return ""
        return str(master_dir / MEMORY_FILE)

    def load(self):
        server_path = self.get_server_path()
        loaded_from = None
        if server_path:
            try:
                if os.path.exists(server_path):
                    with open(server_path, "r", encoding="utf-8") as f:
                        self._data = json.load(f)
                    loaded_from = "NAS"
                    self._save_to_local()
            except Exception as e:
                logger.warning("Failed to load product memory from NAS: %s", e)

        if not loaded_from and self._local_cache_path.exists():
            try:
                self._data = json.loads(self._local_cache_path.read_text(encoding="utf-8"))
                loaded_from = "Cache"
            except Exception as e:
                logger.error("Failed to load product memory cache: %s", e)

        if not self._data:
            self._data = {"products": {}}
        self._loaded = True
        logger.info("Product memory loaded from %s", loaded_from or "Empty")

    def save(self):
        self._save_to_local()
        server_path = self.get_server_path()
        if not server_path:
            return
        try:
            os.makedirs(os.path.dirname(server_path), exist_ok=True)
            with open(server_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save product memory to NAS: %s", e)

    def _save_to_local(self):
        try:
            self._local_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._local_cache_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save local product memory cache: %s", e)

    def learn(self, product: str, type_: str, thickness: str, measure: str):
        if not product:
            return
        product = product.upper().strip()
        type_ = (type_ or "").upper().strip()
        thickness = (thickness or "").upper().strip()
        measure = (measure or "").upper().strip()

        products = self._data.setdefault("products", {})
        p_data = products.setdefault(
            product,
            {"count": 0, "types": {}, "thicknesses": {}, "measures": {}},
        )
        p_data["count"] = int(p_data.get("count", 0)) + 1
        if type_:
            p_data.setdefault("types", {})
            p_data["types"][type_] = int(p_data["types"].get(type_, 0)) + 1
        if thickness:
            p_data.setdefault("thicknesses", {})
            p_data["thicknesses"][thickness] = int(p_data["thicknesses"].get(thickness, 0)) + 1
        if measure:
            p_data.setdefault("measures", {})
            p_data["measures"][measure] = int(p_data["measures"].get(measure, 0)) + 1

    def get_products(self) -> List[str]:
        products = self._data.get("products", {})
        return sorted(products.keys(), key=lambda k: products[k].get("count", 0), reverse=True)

    def get_types(self, product: str) -> List[str]:
        p_data = self._data.get("products", {}).get((product or "").upper().strip(), {})
        types = p_data.get("types", {})
        return sorted(types.keys(), key=lambda k: types[k], reverse=True)

    def get_thicknesses(self, product: str) -> List[str]:
        p_data = self._data.get("products", {}).get((product or "").upper().strip(), {})
        thicks = p_data.get("thicknesses", {})
        return sorted(thicks.keys(), key=lambda k: thicks[k], reverse=True)

    def get_measures(self, product: str) -> List[str]:
        p_data = self._data.get("products", {}).get((product or "").upper().strip(), {})
        measures = p_data.get("measures", {})
        return sorted(measures.keys(), key=lambda k: measures[k], reverse=True)
