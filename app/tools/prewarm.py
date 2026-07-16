from __future__ import annotations

from dataclasses import dataclass

from app.core.cache_manager import get_xlsx_path
from app.core.config import AppConfig
from app.core.config_sync import sync_from_master
from app.core.data_manager import build_index
from app.core.index_cache import compute_signature, load_index_cache, save_index_cache


@dataclass
class PrewarmResult:
    ok: bool
    message: str
    suppliers_count: int = 0


def run_prewarm(force_refresh: bool = False) -> PrewarmResult:
    cfg = AppConfig.load()
    try:
        # Em PC novo, primeiro puxa a configuração central do NAS para que
        # SMTP/IMAP, assinaturas, caminhos e fontes sejam preparados antes de
        # aquecer o cache de fornecedores.
        sync_from_master(cfg)
    except Exception:
        pass

    nas_path = (cfg.nas_master_path or "").strip()
    if nas_path:
        xlsx_path, cache_msg = get_xlsx_path(nas_path, force_refresh=force_refresh)
        if xlsx_path:
            sources = [xlsx_path]
        else:
            sources = list(cfg.xlsx_sources or [])
            cache_msg = f"cache_manager: {cache_msg}"
    else:
        sources = list(cfg.xlsx_sources or [])
        cache_msg = "sem NAS configurado, usando xlsx_sources"

    if not sources:
        return PrewarmResult(False, "Nenhuma fonte de dados disponivel para prewarm", 0)

    sig = compute_signature(sources, cfg.xlsx_sheet_name)
    cached = load_index_cache(sig)
    if cached is not None and not force_refresh:
        _idx, res = cached
        return PrewarmResult(True, f"Indice ja aquecido ({cache_msg})", int(res.suppliers_count))

    idx, res = build_index(sources, sheet_name=cfg.xlsx_sheet_name)
    if res.errors:
        return PrewarmResult(False, "; ".join(res.errors[:2]), int(res.suppliers_count))

    try:
        save_index_cache(sig, idx, res)
    except Exception as e:
        return PrewarmResult(False, f"Falha ao salvar cache de indice: {e}", int(res.suppliers_count))

    return PrewarmResult(True, f"Prewarm concluido ({cache_msg})", int(res.suppliers_count))
