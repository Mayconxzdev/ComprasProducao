from __future__ import annotations
import re
import unicodedata
from typing import List, Set

_WS_RE = re.compile(r"\s+")
_SEP_RE = re.compile(r"[-/_]+")

def normalize_text(s: str) -> str:
    """Lowercase, remove accents, replace common separators with spaces, collapse whitespace."""
    if s is None:
        return ""
    s = str(s).strip().lower()
    # normalize unicode and strip accents
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _SEP_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s

def header_key(s: str) -> str:
    """Normalize header names aggressively for matching."""
    s = normalize_text(s)
    s = s.replace(":", "").replace(".", "")
    s = s.replace("  ", " ")
    return s

def tokenize(s: str) -> List[str]:
    s = normalize_text(s)
    if not s:
        return []
    parts = s.split(" ")
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) == 1 and not p.isdigit():
            continue
        out.append(p)
    return out

def token_set(s: str) -> Set[str]:
    return set(tokenize(s))

def match_score(query_tokens: List[str], target_tokens: Set[str]) -> int:
    """Return number of query tokens that exist in target_tokens."""
    if not query_tokens:
        return 0
    return sum(1 for t in query_tokens if t in target_tokens)


def clean_text(value: object) -> str:
    """Compatibilidade: limpeza simples usada por telas Qt."""
    return str(value or "").strip()
