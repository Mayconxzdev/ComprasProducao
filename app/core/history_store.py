from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import AppConfig, ensure_app_data_dir
from .config_sync import get_master_dir
from .history_exporter import export_history_rows_to_xlsx
from .file_lock import cross_process_file_lock


GLOBAL_FILE = "history_global.jsonl"
OUTBOX_FILE = "outbox_history.jsonl"
MIGRATION_MARKER = "history_migration_v1.done"
LOCK_FILE = "history_global.lock"
HISTORY_XLSX_FILE = "historico_cotacoes.xlsx"
HISTORY_ARCHIVE_DIR = "history_archive"
DEFAULT_HISTORY_EXPORT_DIR = ensure_app_data_dir() / "reports"

logger = logging.getLogger(__name__)

REQUEST_METADATA_FIELDS = (
    "requester_name",
    "approver_name",
    "request_number",
    "release_date",
    "request_purpose",
    "request_department",
)


@dataclass
class HistoryEvent:
    event_id: str
    ts: str
    event_type: str
    status: str
    product_query: str
    subject: str
    body: str
    recipients: List[dict]
    items: List[str]
    user: str
    pc_name: str
    failed_emails: List[str]
    extra: dict


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:24]


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _sanitize_recipient(recipient: object) -> dict:
    src = recipient if isinstance(recipient, dict) else {}
    # Guard-rail: keep only explicit known keys; never infer missing values.
    return {
        "empresa": _clean_text(src.get("empresa") if isinstance(src, dict) else ""),
        "contato_nome": _clean_text(src.get("contato_nome") if isinstance(src, dict) else ""),
        "email": _clean_text(src.get("email") if isinstance(src, dict) else ""),
        "supplier_id": _clean_text(src.get("supplier_id") if isinstance(src, dict) else ""),
    }



def _file_lock(lock_path: Path, timeout_sec: int = 10):
    return cross_process_file_lock(lock_path, timeout_sec=timeout_sec)


class HistoryStore:
    def __init__(self, config: AppConfig):
        self.config = config
        self.user = os.environ.get("USERNAME", "user")
        self.pc_name = platform.node() or "pc"
        self.local_dir = ensure_app_data_dir()
        self.local_cache = self.local_dir / "history_cache.jsonl"
        self.outbox_path = self.local_dir / OUTBOX_FILE
        self.migration_marker = self.local_dir / MIGRATION_MARKER
        self.global_path, self.lock_path = self._resolve_global_paths()
        self._ensure_parent_dirs()
        self._rows_cache: List[dict] | None = None
        self._rows_cache_loaded_at: float = 0.0
        self.migrate_legacy_if_needed()
        self.sync_outbox()

    def _resolve_global_paths(self) -> tuple[Optional[Path], Optional[Path]]:
        master_dir = get_master_dir(self.config)
        if master_dir is None:
            return None, None
        return master_dir / GLOBAL_FILE, master_dir / LOCK_FILE

    def rebind_config(self, config: AppConfig) -> None:
        self.config = config
        self.global_path, self.lock_path = self._resolve_global_paths()
        self._ensure_parent_dirs()

    def _ensure_parent_dirs(self) -> None:
        self.local_dir.mkdir(parents=True, exist_ok=True)
        if self.global_path is not None:
            try:
                self.global_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    def _resolve_export_dir(self) -> Path:
        configured = _clean_text(getattr(self.config, "export_history_default_dir", ""))
        if configured:
            return Path(configured)
        return DEFAULT_HISTORY_EXPORT_DIR

    def _fallback_export_dir(self) -> Path:
        return ensure_app_data_dir() / "reports"

    def export_clean_history_xlsx(self) -> tuple[bool, str, str]:
        rows = self.get_global_history("")
        primary_dir = self._resolve_export_dir()
        primary_file = primary_dir / HISTORY_XLSX_FILE
        try:
            export_history_rows_to_xlsx(rows, primary_file)
            return True, "ok", str(primary_file)
        except Exception as exc:
            fallback_dir = self._fallback_export_dir()
            fallback_file = fallback_dir / HISTORY_XLSX_FILE
            try:
                export_history_rows_to_xlsx(rows, fallback_file)
                return False, f"fallback_local: {exc}", str(fallback_file)
            except Exception as fallback_exc:
                return False, f"falha_export: {exc}; fallback: {fallback_exc}", ""

    def _write_empty_history_files(self) -> None:
        self._write_jsonl_atomic(self.local_cache, [])
        self.outbox_path.unlink(missing_ok=True)
        if self._global_available() and self.global_path is not None:
            with _file_lock(self.lock_path):  # type: ignore[arg-type]
                self._write_jsonl_atomic(self.global_path, [])
        self.invalidate_cache()

    def clear_history_with_archive(self, reason: str = "", actor: str = "") -> tuple[bool, str, dict]:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        meta: dict[str, str | dict] = {
            "reason": _clean_text(reason),
            "actor": _clean_text(actor),
            "timestamp": stamp,
            "archive_local_dir": "",
            "archive_nas_dir": "",
            "files": {},
            "xlsx_path": "",
        }

        local_archive_dir = self.local_dir / HISTORY_ARCHIVE_DIR / stamp
        local_archive_dir.mkdir(parents=True, exist_ok=True)
        meta["archive_local_dir"] = str(local_archive_dir)
        archived_files: dict[str, str] = {}

        try:
            if self.local_cache.exists():
                dst_cache = local_archive_dir / f"history_cache.jsonl.{stamp}.bak"
                shutil.copy2(self.local_cache, dst_cache)
                archived_files["cache"] = str(dst_cache)
            if self.outbox_path.exists():
                dst_outbox = local_archive_dir / f"{OUTBOX_FILE}.{stamp}.bak"
                shutil.copy2(self.outbox_path, dst_outbox)
                archived_files["outbox"] = str(dst_outbox)

            if self._global_available() and self.global_path is not None:
                with _file_lock(self.lock_path):  # type: ignore[arg-type]
                    if self.global_path.exists():
                        dst = local_archive_dir / f"{GLOBAL_FILE}.{stamp}.bak"
                        shutil.copy2(self.global_path, dst)
                        archived_files["global"] = str(dst)
                    self._write_jsonl_atomic(self.global_path, [])

            self._write_jsonl_atomic(self.local_cache, [])
            self.outbox_path.unlink(missing_ok=True)
        except Exception as exc:
            return False, f"falha_limpeza: {exc}", meta

        meta["files"] = archived_files
        self.invalidate_cache()

        nas_archive_dir = self._resolve_export_dir() / "archive" / stamp
        try:
            nas_archive_dir.mkdir(parents=True, exist_ok=True)
            for path_str in archived_files.values():
                src = Path(path_str)
                if src.exists():
                    shutil.copy2(src, nas_archive_dir / src.name)
            meta["archive_nas_dir"] = str(nas_archive_dir)
        except Exception:
            # NAS archive is best-effort; local archive is the source of truth.
            meta["archive_nas_dir"] = ""

        export_ok, export_msg, export_path = self.export_clean_history_xlsx()
        meta["xlsx_path"] = export_path
        if export_ok:
            return True, "historico_limpo", meta
        return True, f"historico_limpo_com_alerta_export: {export_msg}", meta

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
                        if isinstance(row, dict):
                            out.append(row)
                    except Exception:
                        continue
        except Exception:
            return []
        return out

    def _append_jsonl(self, path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_jsonl_atomic(self, path: Path, rows: Iterable[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            suffix=".tmp",
            delete=False,
        ) as tf:
            for row in rows:
                tf.write(json.dumps(row, ensure_ascii=False) + "\n")
            tmp_name = tf.name
        os.replace(tmp_name, str(path))

    def _global_available(self) -> bool:
        return self.global_path is not None and self.lock_path is not None

    def invalidate_cache(self) -> None:
        self._rows_cache = None
        self._rows_cache_loaded_at = 0.0

    def _cache_copy(self) -> List[dict]:
        return [dict(row) for row in list(self._rows_cache or [])]

    def _load_global_rows(self, *, max_age_sec: float = 3.0) -> List[dict]:
        now = time.time()
        if self._rows_cache is not None and (now - self._rows_cache_loaded_at) <= max_age_sec:
            return self._cache_copy()
        if self._global_available():
            try:
                rows = self._read_jsonl(self.global_path)  # type: ignore[arg-type]
                # keep local cache warm
                self._write_jsonl_atomic(self.local_cache, rows)
                self._rows_cache = list(rows)
                self._rows_cache_loaded_at = now
                return [dict(row) for row in rows]
            except Exception:
                pass
        rows = self._read_jsonl(self.local_cache)
        self._rows_cache = list(rows)
        self._rows_cache_loaded_at = now
        return [dict(row) for row in rows]

    def _event_exists(self, event_id: str, rows: Optional[List[dict]] = None) -> bool:
        rows = rows if rows is not None else self._load_global_rows()
        for r in rows:
            if str(r.get("event_id") or "") == event_id:
                return True
        return False

    def append_event(self, event: dict) -> tuple[bool, str]:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            event_id = uuid.uuid4().hex
            event["event_id"] = event_id

        if self._global_available():
            try:
                with _file_lock(self.lock_path):  # type: ignore[arg-type]
                    rows = self._read_jsonl(self.global_path)  # type: ignore[arg-type]
                    if self._event_exists(event_id, rows):
                        return True, "duplicado_ignorado"
                    self._append_jsonl(self.global_path, event)  # type: ignore[arg-type]
                    rows.append(event)
                    self._write_jsonl_atomic(self.local_cache, rows)
                    self.invalidate_cache()
                return True, "ok_global"
            except Exception as e:
                # fallback to outbox
                self._append_jsonl(self.outbox_path, event)
                self.invalidate_cache()
                return False, f"global_offline_outbox: {e}"

        self._append_jsonl(self.outbox_path, event)
        self.invalidate_cache()
        return False, "sem_global_outbox"

    def sync_outbox(self) -> tuple[bool, str]:
        if not self._global_available():
            return False, "global indisponivel"
        pending = self._read_jsonl(self.outbox_path)
        if not pending:
            return True, "nada_para_sincronizar"
        try:
            with _file_lock(self.lock_path):  # type: ignore[arg-type]
                global_rows = self._read_jsonl(self.global_path)  # type: ignore[arg-type]
                ids = {str(r.get("event_id") or "") for r in global_rows}
                wrote = 0
                for row in pending:
                    eid = str(row.get("event_id") or "")
                    if eid and eid in ids:
                        continue
                    self._append_jsonl(self.global_path, row)  # type: ignore[arg-type]
                    global_rows.append(row)
                    if eid:
                        ids.add(eid)
                    wrote += 1
                self._write_jsonl_atomic(self.local_cache, global_rows)
                self.invalidate_cache()
            self.outbox_path.unlink(missing_ok=True)
            return True, f"sincronizados={wrote}"
        except Exception as e:
            return False, f"falha_sync_outbox: {e}"

    def get_global_history(self, query: str = "") -> List[Dict]:
        rows = self._load_global_rows()
        q = (query or "").strip().lower()
        if q:
            def _matches_query(row: dict) -> bool:
                if q in str(row.get("product_query", "")).lower():
                    return True
                if q in str(row.get("status", "")).lower():
                    return True
                if q in str(row.get("event_type", "")).lower():
                    return True
                extra = row.get("extra") or {}
                if isinstance(extra, dict):
                    for key in (
                        "requester_name",
                        "approver_name",
                        "request_number",
                        "release_date",
                        "request_purpose",
                        "request_department",
                    ):
                        if q in str(extra.get(key, "")).lower():
                            return True
                recipients = row.get("recipients") or []
                for recipient in recipients:
                    if q in str(recipient.get("empresa", "")).lower():
                        return True
                    if q in str(recipient.get("email", "")).lower():
                        return True
                return False

            rows = [
                r for r in rows
                if _matches_query(r)
            ]
        rows.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
        return rows

    def get_event_by_id(self, event_id: str) -> Optional[dict]:
        if not event_id:
            return None
        for row in self._load_global_rows():
            if str(row.get("event_id") or "") == event_id:
                return row
        return None

    def set_event_archived(
        self,
        *,
        event_id: str,
        archived: bool,
        actor: str = "",
    ) -> tuple[bool, str, dict]:
        """Archive/reactivate a quote without deleting history.

        The active workflow reads this explicit flag instead of relying on a
        fixed migration date. This keeps Acompanhar predictable and reversible.
        """
        target_id = _clean_text(event_id)
        if not target_id:
            return False, "event_id_invalido", {}

        now_ts = _now_iso()
        actor_name = _clean_text(actor) or self.user

        def _apply(rows: List[dict]) -> tuple[bool, dict]:
            for row in rows:
                if str(row.get("event_id") or "") != target_id:
                    continue
                extra_raw = row.get("extra") or {}
                extra = dict(extra_raw) if isinstance(extra_raw, dict) else {}
                trail = extra.get("archive_updates")
                history_trail = list(trail) if isinstance(trail, list) else []
                history_trail.append({"ts": now_ts, "actor": actor_name, "archived": bool(archived)})
                extra["archive_updates"] = history_trail[-30:]
                if archived:
                    extra["is_archived"] = True
                    extra["archived_at"] = now_ts
                    extra["archived_by"] = actor_name
                else:
                    extra["is_archived"] = False
                    extra.pop("archived_at", None)
                    extra.pop("archived_by", None)
                row["extra"] = extra
                return True, row
            return False, {}

        if self._global_available():
            try:
                with _file_lock(self.lock_path):  # type: ignore[arg-type]
                    rows = self._read_jsonl(self.global_path)  # type: ignore[arg-type]
                    ok, updated = _apply(rows)
                    if not ok:
                        return False, "evento_nao_encontrado", {}
                    self._write_jsonl_atomic(self.global_path, rows)  # type: ignore[arg-type]
                    self._write_jsonl_atomic(self.local_cache, rows)
                    self.invalidate_cache()
                    return True, "arquivado" if archived else "reativado", updated
            except Exception as exc:
                return False, f"falha_arquivar: {exc}", {}

        try:
            rows = self._read_jsonl(self.local_cache)
            ok, updated = _apply(rows)
            if not ok:
                return False, "evento_nao_encontrado", {}
            self._write_jsonl_atomic(self.local_cache, rows)
            self.invalidate_cache()
            return True, "arquivado" if archived else "reativado", updated
        except Exception as exc:
            return False, f"falha_local_arquivar: {exc}", {}

    def update_request_metadata(
        self,
        *,
        event_id: str,
        requester_name: str = "",
        approver_name: str = "",
        request_number: str = "",
        release_date: str = "",
        request_purpose: str = "",
        request_department: str = "",
        actor: str = "",
        actor_pc: str = "",
    ) -> tuple[bool, str, dict]:
        target_id = _clean_text(event_id)
        if not target_id:
            return False, "event_id_invalido", {}

        now_ts = _now_iso()
        actor_name = _clean_text(actor) or self.user
        actor_machine = _clean_text(actor_pc) or self.pc_name
        updates = {
            "requester_name": _clean_text(requester_name),
            "approver_name": _clean_text(approver_name),
            "request_number": _clean_text(request_number),
            "release_date": _clean_text(release_date),
            "request_purpose": _clean_text(request_purpose),
            "request_department": _clean_text(request_department),
        }

        def _apply_update(rows: List[dict]) -> tuple[bool, dict]:
            for row in rows:
                if str(row.get("event_id") or "") != target_id:
                    continue
                extra_raw = row.get("extra") or {}
                extra = dict(extra_raw) if isinstance(extra_raw, dict) else {}
                before = {field: _clean_text(extra.get(field, "")) for field in REQUEST_METADATA_FIELDS}
                for field, value in updates.items():
                    extra[field] = value
                changes = {
                    field: {"from": before[field], "to": updates[field]}
                    for field in REQUEST_METADATA_FIELDS
                    if before[field] != updates[field]
                }
                trail = extra.get("request_updates")
                history_trail = list(trail) if isinstance(trail, list) else []
                history_trail.append(
                    {
                        "ts": now_ts,
                        "actor": actor_name,
                        "pc_name": actor_machine,
                        "changes": changes,
                    }
                )
                extra["request_updates"] = history_trail[-30:]
                extra["request_updated_at"] = now_ts
                extra["request_updated_by"] = actor_name
                extra["request_updated_pc"] = actor_machine
                row["extra"] = extra
                return True, row
            return False, {}

        updated = False
        updated_row: dict = {}

        if self._global_available():
            try:
                with _file_lock(self.lock_path):  # type: ignore[arg-type]
                    rows = self._read_jsonl(self.global_path)  # type: ignore[arg-type]
                    updated, updated_row = _apply_update(rows)
                    if not updated:
                        return False, "evento_nao_encontrado", {}
                    self._write_jsonl_atomic(self.global_path, rows)  # type: ignore[arg-type]
                    self._write_jsonl_atomic(self.local_cache, rows)
                    self.invalidate_cache()
            except Exception as exc:
                return False, f"falha_atualizar_requisicao: {exc}", {}
        else:
            try:
                rows = self._read_jsonl(self.local_cache)
                updated, updated_row = _apply_update(rows)
                if not updated:
                    return False, "evento_nao_encontrado", {}
                self._write_jsonl_atomic(self.local_cache, rows)
                self.invalidate_cache()
            except Exception as exc:
                return False, f"falha_local_atualizar_requisicao: {exc}", {}

        export_ok, export_msg, export_path = self.export_clean_history_xlsx()
        if export_ok:
            logger.info("Historico XLSX atualizado apos editar requisicao: %s", export_path)
        else:
            logger.warning("Historico XLSX export com alerta apos editar requisicao: %s", export_msg)

        message = "ok"
        if export_msg and export_msg != "ok":
            message = f"ok | {export_msg}"
        return True, message, updated_row

    def record_send_event(
        self,
        *,
        status: str,
        product_query: str,
        subject: str,
        body: str,
        recipients: List[dict],
        items: List[str],
        failed_emails: List[str],
        event_type: str = "smtp_send",
        extra: Optional[dict] = None,
    ) -> tuple[bool, str, str]:
        clean_recipients = [_sanitize_recipient(row) for row in list(recipients or [])]
        clean_items = [_clean_text(item) for item in list(items or []) if _clean_text(item)]
        clean_failed = [_clean_text(email) for email in list(failed_emails or []) if _clean_text(email)]
        clean_extra = dict(extra or {})
        base = f"{_clean_text(product_query)}|{_clean_text(subject)}|{_clean_text(status)}|{len(clean_recipients)}|{len(clean_items)}|{_now_iso()}"
        event_id = _stable_id(base, self.pc_name, self.user, uuid.uuid4().hex[:8])
        row = {
            "event_id": event_id,
            "ts": _now_iso(),
            "event_type": event_type,
            "status": _clean_text(status),
            "product_query": _clean_text(product_query),
            "subject": _clean_text(subject),
            "body": _clean_text(body),
            "recipients": clean_recipients,
            "items": clean_items,
            "user": self.user,
            "pc_name": self.pc_name,
            "failed_emails": clean_failed,
            "extra": clean_extra,
        }
        ok, msg = self.append_event(row)
        # Exportar XLSX a cada envio deixava o app pesado em rede/NAS.
        # A exportacao completa continua disponivel em Configuracoes, mas o fluxo
        # de envio fica instantaneo por padrao.
        if bool(getattr(self.config, "history_export_on_send", False)):
            export_ok, export_msg, export_path = self.export_clean_history_xlsx()
            if export_ok:
                logger.info("Historico XLSX atualizado: %s", export_path)
            else:
                logger.warning("Historico XLSX export com alerta: %s", export_msg)
            if export_msg and export_msg != "ok":
                msg = f"{msg} | {export_msg}"
        return ok, msg, event_id

    def register_quote(self, product_query: str, suppliers: List[str]) -> None:
        recipients = [{"empresa": "", "email": s} for s in suppliers]
        self.record_send_event(
            status="generated",
            product_query=product_query,
            subject="",
            body="",
            recipients=recipients,
            items=[],
            failed_emails=[],
            event_type="quote_generated",
        )

    def migrate_legacy_if_needed(self) -> None:
        if self.migration_marker.exists():
            return
        migrated: List[dict] = []
        # 1) local sqlite history.db
        sqlite_path = ensure_app_data_dir() / "history.db"
        if sqlite_path.exists():
            try:
                conn = sqlite3.connect(str(sqlite_path))
                cur = conn.cursor()
                cur.execute("SELECT id, created_at, product_query, subject, body, user_pc, status FROM quotes")
                quotes = cur.fetchall()
                for q in quotes:
                    qid, created_at, product_query, subject, body, user_pc, status = q
                    cur.execute(
                        "SELECT empresa, contato_nome, email, telefone, material_produto FROM quote_recipients WHERE quote_id=?",
                        (qid,),
                    )
                    recs = [
                        {
                            "empresa": r[0],
                            "contato_nome": r[1],
                            "email": r[2],
                            "telefone": r[3],
                            "material_produto": r[4],
                        }
                        for r in cur.fetchall()
                    ]
                    cur.execute("SELECT line_text FROM quote_items WHERE quote_id=?", (qid,))
                    items = [str(x[0]) for x in cur.fetchall()]
                    eid = _stable_id(f"sqlite|{qid}|{created_at}|{product_query}")
                    migrated.append(
                        {
                            "event_id": eid,
                            "ts": str(created_at),
                            "event_type": "legacy_sqlite",
                            "status": str(status),
                            "product_query": str(product_query),
                            "subject": str(subject),
                            "body": str(body),
                            "recipients": recs,
                            "items": items,
                            "user": self.user,
                            "pc_name": str(user_pc),
                            "failed_emails": [],
                            "extra": {"legacy_quote_id": int(qid)},
                        }
                    )
                conn.close()
            except Exception:
                pass

        # 2) legacy json history manager files
        legacy_dirs = [
            ensure_app_data_dir() / "history_db",
        ]
        if self.global_path is not None:
            legacy_dirs.append(self.global_path.parent.parent / "history_db")
        for d in legacy_dirs:
            try:
                if not d.exists():
                    continue
                for fp in d.glob("history_*.json"):
                    try:
                        rows = json.loads(fp.read_text(encoding="utf-8"))
                        if not isinstance(rows, list):
                            continue
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            eid = _stable_id(
                                f"legacyjson|{row.get('date','')}|{row.get('product_query','')}|{row.get('supplier_email','')}|{row.get('supplier','')}"
                            )
                            migrated.append(
                                {
                                    "event_id": eid,
                                    "ts": str(row.get("date") or _now_iso()),
                                    "event_type": "legacy_json",
                                    "status": str(row.get("status") or "COTADO"),
                                    "product_query": str(row.get("product_query") or ""),
                                    "subject": "",
                                    "body": "",
                                    "recipients": [{
                                        "empresa": str(row.get("supplier") or ""),
                                        "email": str(row.get("supplier_email") or ""),
                                    }],
                                    "items": [],
                                    "user": str(row.get("user") or self.user),
                                    "pc_name": str(row.get("pc_name") or self.pc_name),
                                    "failed_emails": [],
                                    "extra": {"obs": row.get("obs", "")},
                                }
                            )
                    except Exception:
                        continue
            except Exception:
                continue

        # Deduplicate in-memory before append
        seen = set()
        deduped = []
        for r in migrated:
            eid = str(r.get("event_id") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            deduped.append(r)
        for row in deduped:
            self.append_event(row)
        self.sync_outbox()
        try:
            self.migration_marker.write_text(_now_iso(), encoding="utf-8")
        except Exception:
            pass
