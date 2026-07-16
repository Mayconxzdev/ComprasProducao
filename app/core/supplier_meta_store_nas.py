from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import AppConfig, ensure_app_data_dir
from .config_sync import get_master_dir
from .utils_text import normalize_text
from .file_lock import cross_process_file_lock

META_FILE = "supplier_meta.jsonl"
META_LOCK = "supplier_meta.lock"
OUTBOX_FILE = "meta_outbox.jsonl"
CACHE_FILE = "supplier_meta_cache.jsonl"


@dataclass
class SupplierMeta:
    supplier_key: str
    status: str = "ATIVO"
    notes: str = ""
    tags: str = ""
    last_used_at: str = ""
    is_favorite: bool = False


def supplier_key_from_obj(supplier) -> str:
    sid = str(getattr(supplier, "supplier_id", "") or "").strip()
    if sid:
        return f"id:{normalize_text(sid)}"
    email = normalize_text(str(getattr(supplier, "email", "") or ""))
    if email and "@" in email:
        return f"email:{email}"
    name = normalize_text(str(getattr(supplier, "empresa", "") or ""))
    city = normalize_text(str(getattr(supplier, "cidade", "") or getattr(supplier, "bairro_cidade", "") or ""))
    uf = normalize_text(str(getattr(supplier, "uf", "") or ""))
    return f"name:{name}|{city}|{uf}"



def _file_lock(lock_path: Path, timeout_sec: int = 10):
    return cross_process_file_lock(lock_path, timeout_sec=timeout_sec)


class SupplierMetaStoreNAS:
    def __init__(self, config: AppConfig):
        self.config = config
        self.local_dir = ensure_app_data_dir()
        self.cache_path = self.local_dir / CACHE_FILE
        self.outbox_path = self.local_dir / OUTBOX_FILE
        self._cache: Dict[str, SupplierMeta] = {}
        self._loaded = False
        self.meta_path, self.lock_path = self._resolve_paths()
        self.sync_outbox()

    def _resolve_paths(self) -> tuple[Optional[Path], Optional[Path]]:
        d = get_master_dir(self.config)
        if d is None:
            return None, None
        return d / META_FILE, d / META_LOCK

    def rebind_config(self, config: AppConfig) -> None:
        self.config = config
        self.meta_path, self.lock_path = self._resolve_paths()

    def _global_available(self) -> bool:
        return self.meta_path is not None and self.lock_path is not None

    def _read_jsonl(self, path: Path) -> List[dict]:
        if not path.exists():
            return []
        out: List[dict] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(row, dict):
                        out.append(row)
        except Exception:
            return []
        return out

    def _append_jsonl(self, path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _event_to_meta(self, ev: dict, current: Optional[SupplierMeta]) -> SupplierMeta:
        key = str(ev.get("supplier_key") or "")
        if current is None:
            current = SupplierMeta(supplier_key=key)
        payload = ev.get("meta") or {}
        if not isinstance(payload, dict):
            payload = {}
        return SupplierMeta(
            supplier_key=key,
            status=str(payload.get("status", current.status) or "ATIVO").upper(),
            notes=str(payload.get("notes", current.notes) or ""),
            tags=str(payload.get("tags", current.tags) or ""),
            last_used_at=str(payload.get("last_used_at", current.last_used_at) or ""),
            is_favorite=bool(payload.get("is_favorite", current.is_favorite)),
        )

    def _rebuild_cache_from_rows(self, rows: List[dict]) -> Dict[str, SupplierMeta]:
        out: Dict[str, SupplierMeta] = {}
        for ev in rows:
            key = str(ev.get("supplier_key") or "")
            if not key:
                continue
            out[key] = self._event_to_meta(ev, out.get(key))
        return out

    def _write_cache(self, events: List[dict]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def _load_events(self) -> List[dict]:
        if self._global_available():
            try:
                events = self._read_jsonl(self.meta_path)  # type: ignore[arg-type]
                self._write_cache(events)
                return events
            except Exception:
                pass
        return self._read_jsonl(self.cache_path)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        events = self._load_events()
        self._cache = self._rebuild_cache_from_rows(events)
        self._loaded = True

    def list_all(self) -> Dict[str, SupplierMeta]:
        self._ensure_loaded()
        return dict(self._cache)

    def get(self, supplier_key: str) -> SupplierMeta:
        self._ensure_loaded()
        key = (supplier_key or "").strip()
        if key not in self._cache:
            self._cache[key] = SupplierMeta(supplier_key=key)
        return self._cache[key]

    def _emit_event(self, supplier_key: str, meta: SupplierMeta) -> dict:
        return {
            "op_id": uuid.uuid4().hex,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "supplier_key": supplier_key,
            "meta": {
                "status": meta.status,
                "notes": meta.notes,
                "tags": meta.tags,
                "last_used_at": meta.last_used_at,
                "is_favorite": meta.is_favorite,
            },
        }

    def set(self, supplier_key: str, meta: SupplierMeta) -> tuple[bool, str]:
        self._ensure_loaded()
        self._cache[supplier_key] = meta
        ev = self._emit_event(supplier_key, meta)
        return self._append_event(ev)

    def _append_event(self, ev: dict) -> tuple[bool, str]:
        if self._global_available():
            try:
                with _file_lock(self.lock_path):  # type: ignore[arg-type]
                    self._append_jsonl(self.meta_path, ev)  # type: ignore[arg-type]
                # warm local cache append
                self._append_jsonl(self.cache_path, ev)
                return True, "ok_global"
            except Exception as e:
                self._append_jsonl(self.outbox_path, ev)
                self._append_jsonl(self.cache_path, ev)
                return False, f"global_offline_outbox:{e}"
        self._append_jsonl(self.outbox_path, ev)
        self._append_jsonl(self.cache_path, ev)
        return False, "sem_global_outbox"

    def bulk_update(self, keys: Iterable[str], **updates) -> tuple[int, str]:
        self._ensure_loaded()
        n = 0
        msg = "ok"
        for key in keys:
            k = (key or "").strip()
            if not k:
                continue
            meta = self.get(k)
            if "status" in updates and updates["status"] is not None:
                meta.status = str(updates["status"]).upper()
            if "notes" in updates and updates["notes"] is not None:
                meta.notes = str(updates["notes"])
            if "tags" in updates and updates["tags"] is not None:
                meta.tags = str(updates["tags"])
            if "is_favorite" in updates and updates["is_favorite"] is not None:
                meta.is_favorite = bool(updates["is_favorite"])
            if "last_used_at" in updates and updates["last_used_at"] is not None:
                meta.last_used_at = str(updates["last_used_at"])
            ok, m = self.set(k, meta)
            if not ok:
                msg = m
            n += 1
        return n, msg

    def touch_last_used(self, supplier_key: str) -> tuple[bool, str]:
        meta = self.get(supplier_key)
        meta.last_used_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return self.set(supplier_key, meta)

    def set_favorite(self, supplier_key: str, value: bool) -> tuple[bool, str]:
        meta = self.get(supplier_key)
        meta.is_favorite = bool(value)
        return self.set(supplier_key, meta)

    def sync_outbox(self) -> tuple[bool, str]:
        if not self._global_available():
            return False, "global_indisponivel"
        pending = self._read_jsonl(self.outbox_path)
        if not pending:
            return True, "nada_para_sync"
        try:
            with _file_lock(self.lock_path):  # type: ignore[arg-type]
                existing = self._read_jsonl(self.meta_path)  # type: ignore[arg-type]
                existing_ids = {str(e.get("op_id") or "") for e in existing}
                wrote = 0
                for ev in pending:
                    op_id = str(ev.get("op_id") or "")
                    if op_id and op_id in existing_ids:
                        continue
                    self._append_jsonl(self.meta_path, ev)  # type: ignore[arg-type]
                    wrote += 1
            self.outbox_path.unlink(missing_ok=True)
            return True, f"sync_ok:{wrote}"
        except Exception as e:
            return False, f"sync_fail:{e}"

    def sync_now(self) -> tuple[bool, str]:
        ok1, msg1 = self.sync_outbox()
        self._loaded = False
        self._ensure_loaded()
        return ok1, msg1
