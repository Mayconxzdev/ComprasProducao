from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from .config import (
    AppConfig,
    SMTPProfile,
    IMAPProfile,
    WebSearchConfig,
    canonical_smtp_profile_label,
    demo_mode_enabled,
    ensure_app_data_dir,
)
from .cache_manager import get_cache_file_path
from .path_utils import nas_fallback_candidates, normalize_master_path
from .file_lock import cross_process_file_lock

logger = logging.getLogger(__name__)


MASTER_CONFIG_FILE = "master_config.json"
MASTER_CONFIG_CACHE_FILE = "master_config_cache.json"
LOCK_FILE = "master_config.lock"
SYNC_VERSION = 2

GLOBAL_FIELDS = {
    "nas_master_path",
    "xlsx_sources",
    "default_subject_prefix",
    "smtp_active_profile",
    "smtp_profiles",
    "imap_profiles",
    "xlsx_sheet_name",
    "web_search",
    "email_signatures",
    "email_signatures_managed",
    "custom_quote_types",
    "hide_pre_tracking_history",
    "history_tracking_cutover_at",
}

LOCAL_ONLY_FIELDS = {
    "search_history",
    "export_history_default_dir",
    "thunderbird_path",
}


def _is_user_local_cache_source(path: str) -> bool:
    norm = str(path or "").replace("/", "\\").casefold()
    return (
        "\\users\\" in norm
        and "\\appdata\\roaming\\comprasapp\\cache\\master.xlsx" in norm
    )


def _is_local_appdata_source(path: str) -> bool:
    norm = str(path or "").replace("/", "\\").casefold()
    return "\\appdata\\roaming\\comprasapp\\" in norm


def _dedupe_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        p = normalize_master_path(str(raw or "").strip())
        if not p:
            continue
        key = p.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _shared_master_sources(config: AppConfig) -> list[str]:
    """
    Build xlsx_sources payload for master config with only shared, machine-agnostic paths.
    """
    shared: list[str] = []

    nas = normalize_master_path(str(config.nas_master_path or "").strip())
    if nas.lower().endswith((".xlsx", ".xlsm")):
        shared.append(nas)

    for raw in list(config.xlsx_sources or []):
        p = normalize_master_path(str(raw or "").strip())
        if not p:
            continue
        if _is_local_appdata_source(p) or _is_user_local_cache_source(p):
            continue
        shared.append(p)

    return _dedupe_paths(shared)


def _runtime_sources_from_master(config: AppConfig, master_sources: list[str]) -> list[str]:
    """
    Build runtime xlsx_sources for current PC.
    Includes NAS source(s) + shared source(s) + this machine local cache fallback.
    """
    runtime: list[str] = []

    nas = normalize_master_path(str(config.nas_master_path or "").strip())
    if nas.lower().endswith((".xlsx", ".xlsm")):
        runtime.append(nas)

    for raw in list(master_sources or []):
        p = normalize_master_path(str(raw or "").strip())
        if not p:
            continue
        if _is_local_appdata_source(p) or _is_user_local_cache_source(p):
            continue
        runtime.append(p)

    try:
        runtime.append(str(get_cache_file_path()))
    except Exception:
        pass

    return _dedupe_paths(runtime)


def _nas_root_from_path(nas_master_path: str) -> Optional[Path]:
    p = (nas_master_path or "").strip()
    if not p:
        return None
    pp = Path(p)
    if pp.suffix.lower() in {".xlsx", ".xlsm"}:
        return pp.parent
    return pp


def _nas_root_candidates(nas_master_path: str) -> list[Path]:
    roots: list[Path] = []
    base = normalize_master_path(nas_master_path or "")
    for candidate in nas_fallback_candidates(base) or ([base] if base else []):
        pp = Path(candidate)
        root = pp.parent if pp.suffix.lower() in {".xlsx", ".xlsm"} else pp
        roots.append(root)
    dedup: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        token = str(root).replace("/", "\\").rstrip("\\").casefold()
        if not token or token in seen:
            continue
        seen.add(token)
        dedup.append(root)
    return dedup


def get_master_dir_candidates(config: AppConfig) -> list[Path]:
    candidates = _nas_root_candidates(config.nas_master_path)
    return [root / "ComprasApp" for root in candidates]


def get_master_dir(config: AppConfig) -> Optional[Path]:
    dirs = get_master_dir_candidates(config)
    if not dirs:
        return None
    for d in dirs:
        try:
            if d.exists():
                return d
        except Exception:
            continue
    return dirs[0]


def get_master_config_path(config: AppConfig) -> Optional[Path]:
    d = get_master_dir(config)
    if not d:
        return None
    return d / MASTER_CONFIG_FILE


def get_master_lock_path(config: AppConfig) -> Optional[Path]:
    d = get_master_dir(config)
    if not d:
        return None
    return d / LOCK_FILE


def get_cached_master_path() -> Path:
    cache_dir = ensure_app_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / MASTER_CONFIG_CACHE_FILE



def _file_lock(lock_path: Path, timeout_sec: int = 10):
    return cross_process_file_lock(lock_path, timeout_sec=timeout_sec)


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    ) as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tmp_name = tf.name
    os.replace(tmp_name, str(path))


def _read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        # Accept UTF-8 with or without BOM (common when edited by Windows tools).
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _profile_to_master(profile_key: str, profile: SMTPProfile) -> dict:
    # Transport metadata is shared; credentials stay local on each PC (DPAPI).
    return {
        "label": canonical_smtp_profile_label(profile_key, profile.label),
        "host": profile.host,
        "port": profile.port,
        "security": profile.security,
        "auth_method": profile.auth_method,
        "username": profile.username,
        "from_email": profile.from_email or profile.username,
        "bcc_email": profile.bcc_email or profile.username,
        "timeout_sec": profile.timeout_sec,
    }


def _imap_profile_to_master(profile_key: str, profile: IMAPProfile) -> dict:
    return {
        "label": profile.label or profile_key,
        "host": profile.host,
        "port": profile.port,
        "security": profile.security,
        "username": profile.username,
        "enabled": bool(profile.enabled),
        "mailbox": profile.mailbox or "INBOX",
        "timeout_sec": profile.timeout_sec or 20,
    }


def _web_search_to_master(config: AppConfig) -> dict:
    ws = config.web_search if isinstance(config.web_search, WebSearchConfig) else WebSearchConfig()
    return {
        "primary_provider": str(ws.primary_provider or "auto").lower(),
        "enable_duckduckgo_search_fallback": bool(getattr(ws, "enable_duckduckgo_search_fallback", True)),
        "enable_heavy_fallback": bool(getattr(ws, "enable_heavy_fallback", True)),
    }


def config_to_master_dict(config: AppConfig) -> dict:
    shared_sources = _shared_master_sources(config)
    payload = {
        "schema": "comprasapp_master_config",
        "version": SYNC_VERSION,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "global": {
            "nas_master_path": config.nas_master_path,
            "xlsx_sources": shared_sources,
            "default_subject_prefix": config.default_subject_prefix,
            "smtp_active_profile": config.smtp_active_profile,
            "xlsx_sheet_name": config.xlsx_sheet_name,
            "smtp_profiles": {
                k: _profile_to_master(k, v) for k, v in config.smtp_profiles.items()
            },
            "imap_profiles": {
                k: _imap_profile_to_master(k, v) for k, v in config.imap_profiles.items()
            },
            "web_search": _web_search_to_master(config),
            "email_signatures": config.email_signatures,
            "email_signatures_managed": bool(config.email_signatures_managed),
            "custom_quote_types": list(config.custom_quote_types or []),
            "hide_pre_tracking_history": bool(config.hide_pre_tracking_history),
            "history_tracking_cutover_at": config.history_tracking_cutover_at,
        },
    }
    return payload


def _preserve_existing_master_secrets(payload: dict, existing_master: dict | None) -> dict:
    # Mantido como ponto de compatibilidade: segredos nunca são preservados ou
    # propagados pelo arquivo compartilhado.
    return payload


def _merge_master_into_config(config: AppConfig, master_data: dict) -> None:
    g = master_data.get("global", {}) if isinstance(master_data, dict) else {}
    if not isinstance(g, dict):
        return
    master_sources: list[str] = []
    if "nas_master_path" in g:
        config.nas_master_path = str(g.get("nas_master_path") or "")
    if "xlsx_sources" in g and isinstance(g["xlsx_sources"], list):
        master_sources = [str(x) for x in g["xlsx_sources"]]
    config.xlsx_sources = _runtime_sources_from_master(config, master_sources)
    if "default_subject_prefix" in g:
        config.default_subject_prefix = str(g.get("default_subject_prefix") or "Cotação")
    if "smtp_active_profile" in g:
        config.smtp_active_profile = str(g.get("smtp_active_profile") or config.smtp_active_profile)
    if "xlsx_sheet_name" in g:
        config.xlsx_sheet_name = str(g.get("xlsx_sheet_name") or config.xlsx_sheet_name)
    web_search = g.get("web_search")
    if isinstance(web_search, dict):
        ws = config.web_search if isinstance(config.web_search, WebSearchConfig) else WebSearchConfig()
        ws.set_primary_provider(str(web_search.get("primary_provider") or ws.primary_provider))
        # API keys are intentionally never imported from shared configuration.
        ws.brave_api_key_shared_b64 = ""
        ws.brave_api_key_protected_b64 = ""
        ws.enable_duckduckgo_search_fallback = bool(web_search.get("enable_duckduckgo_search_fallback", ws.enable_duckduckgo_search_fallback))
        ws.enable_heavy_fallback = bool(web_search.get("enable_heavy_fallback", ws.enable_heavy_fallback))
        config.web_search = ws

    smtp_profiles = g.get("smtp_profiles")
    if isinstance(smtp_profiles, dict):
        out: dict[str, SMTPProfile] = {}
        for name, p in smtp_profiles.items():
            if not isinstance(p, dict):
                continue
            out[name] = SMTPProfile(
                label=str(p.get("label") or name),
                host=str(p.get("host") or ""),
                port=int(p.get("port") or 465),
                security=str(p.get("security") or "ssl"),
                auth_method=str(p.get("auth_method") or "password"),
                username=str(p.get("username") or ""),
                from_email=str(p.get("from_email") or ""),
                bcc_email=str(p.get("bcc_email") or ""),
                timeout_sec=int(p.get("timeout_sec") or 20),
                password_protected_b64="",
                shared_password_b64="",
            )
        if out:
            config.smtp_profiles = out
            config.normalize_smtp_profile_labels()

    imap_profiles = g.get("imap_profiles")
    if isinstance(imap_profiles, dict):
        out_imap: dict[str, IMAPProfile] = {}
        for name, p in imap_profiles.items():
            if not isinstance(p, dict):
                continue
            out_imap[name] = IMAPProfile(
                label=str(p.get("label") or name),
                host=str(p.get("host") or ""),
                port=int(p.get("port") or 993),
                security=str(p.get("security") or "ssl"),
                username=str(p.get("username") or ""),
                enabled=bool(p.get("enabled", True)),
                mailbox=str(p.get("mailbox") or "INBOX"),
                timeout_sec=int(p.get("timeout_sec") or 20),
                password_protected_b64="",
                shared_password_b64="",
            )
        if out_imap:
            config.imap_profiles = out_imap

    email_signatures = g.get("email_signatures")
    if isinstance(email_signatures, dict):
        clean_sigs: dict[str, dict[str, str]] = {}
        for user, profiles in email_signatures.items():
            if not isinstance(profiles, dict):
                continue
            clean_sigs[str(user).lower()] = {
                str(profile_key): str(profile_path)
                for profile_key, profile_path in profiles.items()
            }
        config.email_signatures = clean_sigs
    if "email_signatures_managed" in g:
        config.email_signatures_managed = bool(g.get("email_signatures_managed"))
    elif "email_signatures" in g:
        config.email_signatures_managed = True
    custom_quote_types = g.get("custom_quote_types")
    if isinstance(custom_quote_types, list):
        clean_types: list[dict[str, object]] = []
        for item in custom_quote_types:
            if isinstance(item, dict):
                clean_types.append(dict(item))
        config.custom_quote_types = clean_types[:24]

    if "hide_pre_tracking_history" in g:
        config.hide_pre_tracking_history = bool(g.get("hide_pre_tracking_history"))
    if "history_tracking_cutover_at" in g:
        config.history_tracking_cutover_at = str(g.get("history_tracking_cutover_at") or "")

    if config.smtp_active_profile not in config.smtp_profiles and config.smtp_profiles:
        config.smtp_active_profile = next(iter(config.smtp_profiles.keys()))


def _cache_master(master_data: dict) -> None:
    cache_path = get_cached_master_path()
    _atomic_write_json(cache_path, master_data)


def _load_cached_master() -> Optional[dict]:
    return _read_json(get_cached_master_path())


def sync_from_master(config: AppConfig) -> tuple[bool, str]:
    """
    Pull master config from NAS. If NAS unavailable, fallback to cached master.
    """
    if demo_mode_enabled():
        return True, "Modo demonstração: sincronização de rede ignorada."

    master_dirs = get_master_dir_candidates(config)
    if not master_dirs:
        cached = _load_cached_master()
        if cached:
            _merge_master_into_config(config, cached)
            config.save()
            return False, "NAS nao configurado. Usando cache local do master."
        return False, "NAS nao configurado e sem cache local."

    attempted: list[str] = []
    for base_dir in master_dirs:
        master_path = base_dir / MASTER_CONFIG_FILE
        attempted.append(str(master_path))
        master_data = _read_json(master_path)
        if master_data is None:
            continue
        _merge_master_into_config(config, master_data)
        config.save()
        _cache_master(master_data)
        return True, f"Configuracao global sincronizada do NAS ({master_path})."

    cached = _load_cached_master()
    if cached is not None:
        _merge_master_into_config(config, cached)
        config.save()
        return False, f"NAS indisponivel ({' | '.join(attempted)}). Usando cache local do master."
    return False, f"NAS indisponivel e sem cache local ({' | '.join(attempted)})."


def save_to_master(config: AppConfig) -> tuple[bool, str]:
    """
    Push local global config to NAS master, with lock and atomic write.
    """
    master_dirs = get_master_dir_candidates(config)
    if not master_dirs:
        return False, "NAS nao configurado para salvar master."

    payload = config_to_master_dict(config)
    last_error = ""
    attempted: list[str] = []
    for base_dir in master_dirs:
        master_path = base_dir / MASTER_CONFIG_FILE
        lock_path = base_dir / LOCK_FILE
        attempted.append(str(master_path))
        try:
            with _file_lock(lock_path, timeout_sec=10):
                payload_to_write = _preserve_existing_master_secrets(payload, _read_json(master_path))
                _atomic_write_json(master_path, payload_to_write)
            _cache_master(payload_to_write)
            return True, f"Configuracao global salva no NAS ({master_path})."
        except Exception as e:
            last_error = str(e)
            logger.error("save_to_master failed on %s: %s", master_path, e, exc_info=True)

    # keep local cache updated even when NAS write fails
    try:
        _cache_master(payload)
    except Exception:
        pass
    detail = f" | tentativas: {' | '.join(attempted)}" if attempted else ""
    return False, f"Falha ao salvar no NAS: {last_error or 'sem detalhe'}{detail}"
