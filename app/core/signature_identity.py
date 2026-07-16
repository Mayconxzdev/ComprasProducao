from __future__ import annotations

import getpass
import os
import platform
from dataclasses import dataclass

from app.core.utils_text import normalize_text


@dataclass(frozen=True)
class SignatureIdentity:
    pc_name: str
    windows_user: str
    lookup_keys: tuple[str, ...]

    @property
    def machine(self) -> str:
        return self.pc_name

    @property
    def username(self) -> str:
        return self.windows_user

    @property
    def compound_key(self) -> str:
        if self.lookup_keys:
            return self.lookup_keys[0]
        pc_norm = normalize_text(self.pc_name)
        user_norm = normalize_text(self.windows_user)
        if pc_norm and user_norm:
            return f"{pc_norm}\\{user_norm}"
        return user_norm or pc_norm


def current_signature_identity() -> SignatureIdentity:
    """Return a robust PC/user identity for signature auto-detection."""
    user = ""
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    pc = platform.node() or os.environ.get("COMPUTERNAME") or ""

    pc_norm = normalize_text(pc)
    user_norm = normalize_text(user)
    keys: list[str] = []
    if pc_norm and user_norm:
        keys.append(f"{pc_norm}\\{user_norm}")
    if user_norm:
        keys.append(user_norm)
    if pc_norm:
        keys.append(pc_norm)
    return SignatureIdentity(pc_name=pc, windows_user=user, lookup_keys=tuple(keys))


def resolve_signature_owner(config: object, fallback: str = "") -> tuple[str, str]:
    """
    Resolve signature owner using config.signature_auto_map.
    Supports keys like PC\\user, user, and PC. Values are signature owner names.
    """
    identity = current_signature_identity()
    raw_map = getattr(config, "signature_auto_map", {}) or {}
    clean_map = {}
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            k = normalize_text(str(key or ""))
            v = normalize_text(str(value or ""))
            if k and v:
                clean_map[k] = v

    for key in identity.lookup_keys:
        if key in clean_map:
            return clean_map[key], f"Detectado por {key}"

    last = normalize_text(getattr(config, "last_signature_owner", "") or "")
    if last:
        return last, "Última assinatura usada neste computador"

    default_owner = normalize_text(getattr(config, "default_signature_owner", "") or "")
    if default_owner:
        return default_owner, "Assinatura padrão"

    return normalize_text(fallback), "Selecione a assinatura"
