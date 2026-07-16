"""
Cache Manager for NAS XLSX files

Manages local caching of master XLSX file from NAS to improve:
- Reliability (no network dependency after first load)
- Performance (read from local disk)
- Multi-PC support (each PC has own cache)
"""
import os
import shutil
import logging
import json
import time
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

from .path_utils import first_existing_nas_path, nas_fallback_candidates

logger = logging.getLogger(__name__)
_MASTER_SYNC_VERSION_FILE = "master_sync_version.json"


def get_cache_dir() -> Path:
    """Get cache directory in %APPDATA%"""
    appdata = Path(os.environ.get('APPDATA', ''))
    cache_dir = appdata / 'ComprasApp' / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cache_file_path() -> Path:
    """Get path to cached XLSX file"""
    return get_cache_dir() / 'master.xlsx'


def _source_marker_path(source_path: str) -> Path:
    return Path(source_path).parent / _MASTER_SYNC_VERSION_FILE


def _cache_marker_path() -> Path:
    return get_cache_dir() / _MASTER_SYNC_VERSION_FILE


def _read_marker_token(marker_path: Path) -> str:
    if not marker_path.exists():
        return ""
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("revision", "updated_ts", "updated_at"):
                value = data.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
    except Exception:
        pass
    try:
        return str(int(marker_path.stat().st_mtime))
    except Exception:
        return ""


def check_master_version_marker_changed(source_path: str) -> Tuple[bool, str]:
    """
    Compare NAS master sync marker with local cached marker.

    Returns: (needs_update, reason)
    """
    source_marker = _source_marker_path(source_path)
    cache_marker = _cache_marker_path()
    if not source_marker.exists():
        return False, "Source marker not found"
    source_token = _read_marker_token(source_marker)
    if not source_token:
        return False, "Source marker unreadable"
    cache_token = _read_marker_token(cache_marker)
    if source_token != cache_token:
        return True, "Master sync marker changed"
    return False, "Master sync marker unchanged"


def sync_master_version_marker(source_path: str) -> None:
    source_marker = _source_marker_path(source_path)
    cache_marker = _cache_marker_path()
    if not source_marker.exists():
        return
    cache_marker.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_marker, cache_marker)


def check_file_changed(source_path: str, cache_path: Path) -> Tuple[bool, str]:
    """
    Check if source file has changed compared to cache

    Returns: (needs_update, reason)
    """
    if not cache_path.exists():
        return True, "Cache file doesn't exist"

    if not os.path.exists(source_path):
        return False, f"Source file not found: {source_path}"

    try:
        # Compare modification time
        source_mtime = os.path.getmtime(source_path)
        cache_mtime = cache_path.stat().st_mtime

        # Compare file size
        source_size = os.path.getsize(source_path)
        cache_size = cache_path.stat().st_size

        if source_mtime > cache_mtime:
            return True, f"Source file is newer (source: {datetime.fromtimestamp(source_mtime)}, cache: {datetime.fromtimestamp(cache_mtime)})"

        if source_size != cache_size:
            return True, f"File size differs (source: {source_size}, cache: {cache_size})"

        return False, "Cache is up to date"

    except Exception as e:
        logger.warning(f"Error comparing files: {e}")
        return False, str(e)


def update_cache(source_path: str, force: bool = False) -> Tuple[bool, str, Path]:
    """
    Update cache from NAS if needed

    Args:
        source_path: Path to NAS XLSX file
        force: Force update even if cache is current

    Returns: (success, message, cache_path)
    """
    cache_path = get_cache_file_path()

    # Resolve source with NAS host/IP fallback.
    source_candidates = nas_fallback_candidates(source_path)
    source_effective = first_existing_nas_path(source_path)
    if source_effective and source_effective != source_path:
        logger.info("NAS fallback em uso para cache XLSX: %s -> %s", source_path, source_effective)
    source_to_use = source_effective or source_path

    # Check if source exists
    if not os.path.exists(source_to_use):
        if cache_path.exists():
            logger.warning(f"Source not available, using cached file: {cache_path}")
            return True, f"Using cached file (source unavailable)", cache_path
        else:
            attempted = " | ".join(source_candidates) if source_candidates else source_path
            return False, f"Source file not found and no cache available: {attempted}", cache_path

    # Check if update needed
    if not force:
        needs_update, reason = check_file_changed(source_to_use, cache_path)
        marker_changed, marker_reason = check_master_version_marker_changed(source_to_use)
        if marker_changed:
            needs_update = True
            reason = marker_reason
        if not needs_update:
            logger.info(f"Cache is current: {reason}")
            return True, f"Cache up-to-date", cache_path

    # Copy file (with short retry/backoff for transient NAS hiccups)
    try:
        logger.info(f"Updating cache from {source_to_use}")
        _copy_with_retries(source_to_use, cache_path, attempts=3, delay_sec=0.45)
        try:
            sync_master_version_marker(source_to_use)
        except Exception:
            pass
        logger.info(f"Cache updated successfully: {cache_path}")
        return True, "Cache updated from NAS", cache_path

    except PermissionError as e:
        logger.error(f"Permission denied copying file: {e}")
        if cache_path.exists():
            return True, f"Using cached file (update failed: permission denied)", cache_path
        return False, f"Permission denied and no cache available", cache_path

    except Exception as e:
        logger.error(f"Error updating cache: {e}")
        if cache_path.exists():
            return True, f"Using cached file (update failed: {e})", cache_path
        return False, f"Cache update failed: {e}", cache_path


def get_xlsx_path(nas_path: str, force_refresh: bool = False) -> Tuple[Optional[str], str]:
    """
    Get path to XLSX file (from cache, updating from NAS if needed)

    Args:
        nas_path: Path to NAS XLSX file
        force_refresh: Force refresh from NAS

    Returns: (xlsx_path or None, status_message)
    """
    success, message, cache_path = update_cache(nas_path, force=force_refresh)

    if success:
        return str(cache_path), message
    else:
        return None, message


def clear_cache() -> bool:
    """Clear cache directory"""
    try:
        cache_file = get_cache_file_path()
        if cache_file.exists():
            cache_file.unlink()
            logger.info("Cache cleared")
            return True
        return False
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        return False


def _copy_with_retries(source_path: str, cache_path: Path, *, attempts: int = 3, delay_sec: float = 0.45) -> None:
    last_error: Exception | None = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            shutil.copy2(source_path, cache_path)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            logger.warning(
                "Retry copy NAS->cache (%s/%s) after error: %s",
                attempt,
                attempts,
                exc,
            )
            time.sleep(max(0.0, float(delay_sec)))
    if last_error is not None:
        raise last_error
