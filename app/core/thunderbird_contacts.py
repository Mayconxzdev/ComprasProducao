from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable

from .config import ensure_app_data_dir
from .utils_text import normalize_text


DEFAULT_THUNDERBIRD_PROFILE_DIR = ""
THUNDERBIRD_CONTACTS_CACHE = "thunderbird_contacts.json"
_CONTACT_DB_CANDIDATES = (
    "abook.sqlite",
    "history.sqlite",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _cache_path() -> Path:
    cache_dir = ensure_app_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / THUNDERBIRD_CONTACTS_CACHE


def _db_score(profile_dir: Path) -> tuple[int, int]:
    history_size = 0
    total_size = 0
    for db_name in _CONTACT_DB_CANDIDATES:
        db_path = profile_dir / db_name
        if not db_path.exists():
            continue
        size = int(db_path.stat().st_size)
        total_size += size
        if db_name == "history.sqlite":
            history_size = size
    return history_size, total_size


def resolve_thunderbird_profile_dir(profile_dir: str) -> Path | None:
    base = Path(str(profile_dir or "").strip())
    if not base.exists():
        return None

    if any((base / db_name).exists() for db_name in _CONTACT_DB_CANDIDATES):
        return base

    candidates: list[Path] = []
    try:
        for child in base.iterdir():
            if not child.is_dir():
                continue
            if any((child / db_name).exists() for db_name in _CONTACT_DB_CANDIDATES):
                candidates.append(child)
    except Exception:
        return None

    if not candidates:
        return None

    candidates.sort(key=lambda path: (_db_score(path)[0], _db_score(path)[1], path.name.casefold()), reverse=True)
    return candidates[0]


def load_cached_thunderbird_contacts() -> list[dict]:
    path = _cache_path()
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]
    except Exception:
        return []


def _write_cache(rows: Iterable[dict]) -> None:
    path = _cache_path()
    payload = [row for row in rows if isinstance(row, dict)]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_profile_sqlites(profile_dir: str) -> list[Path]:
    source_dir = resolve_thunderbird_profile_dir(profile_dir)
    if source_dir is None or not source_dir.exists():
        return []

    temp_dir = Path(tempfile.mkdtemp(prefix="comprasapp_tb_"))
    copied: list[Path] = []

    candidate_names = [*_CONTACT_DB_CANDIDATES]
    candidate_names.extend(sorted(path.name for path in source_dir.glob("abook-*.sqlite")))

    for db_name in candidate_names:
        sqlite_file = source_dir / db_name
        if not sqlite_file.exists():
            continue
        target = temp_dir / db_name
        shutil.copy2(sqlite_file, target)
        copied.append(target)
        for suffix in ("-wal", "-shm"):
            extra = source_dir / f"{db_name}{suffix}"
            if not extra.exists():
                continue
            try:
                shutil.copy2(extra, temp_dir / f"{db_name}{suffix}")
            except Exception:
                continue
    return copied


def _pick_first(card: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = _clean(card.get(key))
        if value:
            return value
    return ""


def _split_camel_case(text: str) -> str:
    return re.sub(r"(?<=[a-zÀ-ÿ])(?=[A-ZÀ-Ý])", " ", text)


def _dedupe_repeated_token_block(tokens: list[str]) -> list[str]:
    current = list(tokens)
    while current and len(current) % 2 == 0:
        half = len(current) // 2
        if current[:half] != current[half:]:
            break
        current = current[:half]
    return current


def _sanitize_name_like_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", " ", raw, flags=re.IGNORECASE)
    cleaned = cleaned.replace("\\r", " ").replace("\\n", " ")
    cleaned = re.sub(r"[\"'<>|]+", " ", cleaned)
    cleaned = re.sub(r"[-_/]+", " ", cleaned)
    cleaned = _split_camel_case(cleaned)
    tokens = [token.strip() for token in cleaned.split() if token.strip()]
    tokens = _dedupe_repeated_token_block(tokens)
    return " ".join(tokens).strip()


def _parse_vcard(vcard_text: str) -> dict[str, str]:
    text = str(vcard_text or "").strip()
    if not text:
        return {}

    parsed: dict[str, str] = {}
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        head, value = line.split(":", 1)
        key = head.split(";", 1)[0].strip().upper()
        clean_value = _clean(value)
        if not clean_value:
            continue
        parsed.setdefault(key, clean_value)
    return parsed


def _normalize_contact(card: dict[str, str]) -> dict | None:
    email = ""
    for key, value in card.items():
        if "email" not in normalize_text(key):
            continue
        email = _clean(value)
        if email:
            break
    if "@" not in email:
        vcard = _parse_vcard(card.get("_vCard", ""))
        email = _clean(vcard.get("EMAIL"))
    if "@" not in email:
        return None

    name = _pick_first(card, "DisplayName", "PreferDisplayName", "NickName")
    if not name:
        first_name = _pick_first(card, "FirstName")
        last_name = _pick_first(card, "LastName")
        name = " ".join(part for part in (first_name, last_name) if part).strip()
    if not name:
        vcard = _parse_vcard(card.get("_vCard", ""))
        name = _clean(vcard.get("FN"))
    if not name:
        vcard = _parse_vcard(card.get("_vCard", ""))
        raw_n = _clean(vcard.get("N"))
        if raw_n:
            parts = [part.strip() for part in raw_n.split(";") if part.strip()]
            if parts:
                if len(parts) >= 2:
                    name = " ".join(part for part in (parts[1], parts[0]) if part).strip()
                else:
                    name = parts[0]
    name = _sanitize_name_like_text(name)

    company = _pick_first(card, "Company", "Organization", "Department")
    if not company and name and "@" in email:
        company = ""
    if not company:
        vcard = _parse_vcard(card.get("_vCard", ""))
        company = _clean(vcard.get("ORG"))
    company = _sanitize_name_like_text(company)

    return {
        "name": name,
        "company": company,
        "email": email,
        "source": "thunderbird",
    }


def _load_contacts_from_db(copied_db: Path) -> list[dict]:
    contacts: list[dict] = []
    cards: dict[str, dict[str, str]] = {}

    try:
        con = sqlite3.connect(str(copied_db))
        cur = con.cursor()
        rows = cur.execute("SELECT card, name, value FROM properties").fetchall()
        for card_id, name, value in rows:
            card_key = _clean(card_id)
            prop_name = _clean(name)
            if not card_key or not prop_name:
                continue
            cards.setdefault(card_key, {})[prop_name] = _clean(value)
        con.close()
    except Exception:
        return []

    for card in cards.values():
        row = _normalize_contact(card)
        if row:
            contacts.append(row)
    return contacts


def load_thunderbird_contacts(profile_dir: str) -> list[dict]:
    copied_dbs = _copy_profile_sqlites(profile_dir)
    if not copied_dbs:
        return []

    contacts: list[dict] = []
    temp_dir = copied_dbs[0].parent

    try:
        for copied_db in copied_dbs:
            contacts.extend(_load_contacts_from_db(copied_db))
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    seen: set[str] = set()
    deduped: list[dict] = []
    for row in contacts:
        email_key = normalize_text(row.get("email"))
        if not email_key or email_key in seen:
            continue
        seen.add(email_key)
        deduped.append(row)

    deduped.sort(key=lambda row: (normalize_text(row.get("name")), normalize_text(row.get("email"))))
    return deduped


def refresh_thunderbird_contacts_cache(profile_dir: str) -> tuple[bool, str, list[dict]]:
    profile = str(profile_dir or "").strip()
    if not profile:
        cached = load_cached_thunderbird_contacts()
        return False, "perfil_thunderbird_nao_configurado", cached

    contacts = load_thunderbird_contacts(profile)
    if contacts:
        _write_cache(contacts)
        return True, f"contatos={len(contacts)}", contacts

    cached = load_cached_thunderbird_contacts()
    if cached:
        return False, f"perfil_sem_contatos_ou_indisponivel | cache={len(cached)}", cached
    return False, "perfil_sem_contatos_ou_indisponivel", []
