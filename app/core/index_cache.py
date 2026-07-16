from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from .config import ensure_app_data_dir
from .data_manager import LoadResult


CACHE_VERSION = 1


def _cache_file() -> Path:
    d = ensure_app_data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"index_cache_v{CACHE_VERSION}.pkl"


def _file_stamp(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False, "size": 0, "mtime": 0}
    st = p.stat()
    return {
        "path": str(p.resolve()).lower(),
        "exists": True,
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
    }


def compute_signature(xlsx_sources: list[str], sheet_name: str) -> str:
    payload = {
        "v": CACHE_VERSION,
        "sheet": sheet_name or "",
        "sources": [_file_stamp(s) for s in (xlsx_sources or [])],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_index_cache(signature: str) -> Optional[Tuple[Any, LoadResult]]:
    f = _cache_file()
    if not f.exists():
        return None
    try:
        with f.open("rb") as fp:
            data = pickle.load(fp)
        if not isinstance(data, dict):
            return None
        if data.get("signature") != signature:
            return None
        idx = data.get("index")
        meta = data.get("meta") or {}
        res = LoadResult(
            suppliers_count=int(meta.get("suppliers_count", 0)),
            warnings=list(meta.get("warnings", [])),
            errors=list(meta.get("errors", [])),
            loaded_files=list(meta.get("loaded_files", [])),
            finished_at=float(meta.get("finished_at", time.time())),
            schema_type=str(meta.get("schema_type", "simple")),
        )
        return idx, res
    except Exception:
        return None


def save_index_cache(signature: str, index: Any, result: LoadResult) -> None:
    f = _cache_file()
    payload = {
        "signature": signature,
        "saved_at": int(time.time()),
        "index": index,
        "meta": {
            "suppliers_count": int(result.suppliers_count),
            "warnings": list(result.warnings),
            "errors": list(result.errors),
            "loaded_files": list(result.loaded_files),
            "finished_at": float(result.finished_at),
            "schema_type": str(result.schema_type),
        },
    }
    tmp = f.with_suffix(".tmp")
    with tmp.open("wb") as fp:
        pickle.dump(payload, fp, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, f)
