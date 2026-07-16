from __future__ import annotations

import functools
import os
import re
import subprocess


DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
NET_USE_LINE_RE = re.compile(r"^\s*(?:[A-Za-z]+\s+)?([A-Za-z]:)\s+(\\\\[^\s]+)")
NAS_PRIMARY_HOST = "FILESERVER"
NAS_FALLBACK_HOST = "FILESERVER-BACKUP"


@functools.lru_cache(maxsize=1)
def _mapped_drives() -> dict[str, str]:
    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    out = subprocess.check_output(
        ["cmd", "/c", "net use"],
        text=True,
        timeout=2,
        creationflags=creationflags,
    )
    mappings: dict[str, str] = {}
    for line in out.splitlines():
        match = NET_USE_LINE_RE.match(line)
        if not match:
            continue
        drive = match.group(1).upper()
        mappings[drive] = match.group(2)
    return mappings


def mapped_drive_to_unc(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    if raw.startswith("\\\\"):
        return raw

    m = DRIVE_RE.match(raw)
    if not m:
        return raw
    drive = f"{m.group(1).upper()}:"
    rest = m.group(2).replace("/", "\\")
    try:
        unc_root = _mapped_drives().get(drive)
        if unc_root:
            return f"{unc_root}\\{rest}" if rest else unc_root
    except Exception:
        return raw
    return raw


def normalize_master_path(path: str) -> str:
    p = mapped_drive_to_unc(path)
    if len(p) >= 2 and p[1] == ":":
        return p.replace("/", "\\")
    if p.startswith("\\\\"):
        return p.replace("/", "\\")
    return p


def nas_fallback_candidates(path: str) -> list[str]:
    """
    Build UNC candidates for NAS access, supporting host-name and IP fallback.
    """
    raw = normalize_master_path(path or "")
    if not raw:
        return []
    candidates: list[str] = [raw]
    p_norm = raw.replace("/", "\\")
    host_a = f"\\\\{NAS_PRIMARY_HOST}\\"
    host_b = f"\\\\{NAS_FALLBACK_HOST}\\"
    if p_norm.upper().startswith(host_a.upper()):
        candidates.append(host_b + p_norm[len(host_a):])
    elif p_norm.upper().startswith(host_b.upper()):
        candidates.append(host_a + p_norm[len(host_b):])
    dedup: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup


def first_existing_nas_path(path: str) -> str:
    for candidate in nas_fallback_candidates(path):
        try:
            if os.path.exists(candidate):
                return candidate
        except Exception:
            continue
    return normalize_master_path(path or "")
