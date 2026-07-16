from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from PySide6.QtCore import QObject, QRunnable, Signal
except Exception:  # permite testar fila fora da interface Qt
    class QObject:
        def __init__(self, *args, **kwargs):
            pass
    class QRunnable:
        def __init__(self, *args, **kwargs):
            pass
    class _DummySignal:
        def connect(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass
    def Signal(*args, **kwargs):
        return _DummySignal()

from .config import AppConfig, ensure_app_data_dir
from .smtp_handler import send_email_with_profile


@dataclass
class QueueSummary:
    sent: int = 0
    failed: int = 0
    pending: int = 0


class _QueueSignals(QObject):
    done = Signal(object)
    error = Signal(str)


def _db_path() -> Path:
    return ensure_app_data_dir() / "smtp_queue.sqlite3"


RETRY_DELAYS_SECONDS = (0, 60, 300, 900, 3600)
STUCK_SENDING_AFTER_SECONDS = 1800


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(smtp_queue)").fetchall()}
    if "message_id" not in cols:
        conn.execute("ALTER TABLE smtp_queue ADD COLUMN message_id TEXT")
    if "state_note" not in cols:
        conn.execute("ALTER TABLE smtp_queue ADD COLUMN state_note TEXT")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=20)
    conn.row_factory = sqlite3.Row
    # Banco local: WAL é correto no disco da estação, com durabilidade FULL para a fila.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS smtp_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'pending',
            profile_key TEXT NOT NULL,
            recipients_json TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            body_html TEXT,
            attachments_json TEXT,
            tracking_id TEXT,
            message_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 6,
            next_attempt_at REAL NOT NULL,
            last_error TEXT,
            state_note TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    _ensure_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smtp_queue_status_next ON smtp_queue(status, next_attempt_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smtp_queue_tracking ON smtp_queue(tracking_id)")
    conn.commit()
    return conn


def enqueue_email(
    *,
    profile_key: str,
    recipients: list[str],
    subject: str,
    body: str,
    body_html: str = "",
    attachments: list[str] | None = None,
    tracking_id: str = "",
    error: str = "",
    max_attempts: int = 6,
) -> int:
    now = time.time()
    clean_recipients = [str(r).strip() for r in recipients if str(r).strip()]
    if not clean_recipients:
        return 0
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO smtp_queue
            (status, profile_key, recipients_json, subject, body, body_html, attachments_json, tracking_id,
             attempts, max_attempts, next_attempt_at, last_error, created_at, updated_at)
            VALUES ('pending',?,?,?,?,?,?,?,0,?,?,?, ?, ?)
            """,
            (
                str(profile_key or "vesper"),
                json.dumps(clean_recipients, ensure_ascii=False),
                str(subject or ""),
                str(body or ""),
                str(body_html or ""),
                json.dumps(list(attachments or []), ensure_ascii=False),
                str(tracking_id or ""),
                int(max_attempts or 6),
                now,
                str(error or ""),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def pending_count() -> int:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM smtp_queue WHERE status IN ('pending','sending')").fetchone()
            return int(row["n"] if row else 0)
    except Exception:
        return 0


def _retry_delay_seconds(attempts: int) -> int:
    idx = max(0, min(int(attempts), len(RETRY_DELAYS_SECONDS) - 1))
    return int(RETRY_DELAYS_SECONDS[idx])


def process_pending_queue(config: AppConfig, *, limit: int = 8) -> QueueSummary:
    summary = QueueSummary()
    now = time.time()
    with _connect() as conn:
        # Recupera envio que ficou marcado como “sending” após queda de energia/fechamento.
        conn.execute(
            "UPDATE smtp_queue SET status='pending', state_note='recuperado_de_sending', updated_at=? "
            "WHERE status='sending' AND updated_at < ?",
            (now, now - STUCK_SENDING_AFTER_SECONDS),
        )
        rows = conn.execute(
            """
            SELECT * FROM smtp_queue
            WHERE status='pending' AND next_attempt_at <= ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
        summary.pending = pending_count()
        for row in rows:
            row_id = int(row["id"])
            attempts = int(row["attempts"] or 0) + 1
            max_attempts = int(row["max_attempts"] or 6)
            conn.execute(
                "UPDATE smtp_queue SET status='sending', attempts=?, updated_at=?, state_note='worker_enviando' WHERE id=? AND status='pending'",
                (attempts, time.time(), row_id),
            )
            conn.commit()
            old_profile = str(getattr(config, "smtp_active_profile", "vesper") or "vesper")
            try:
                recipients = json.loads(row["recipients_json"] or "[]")
                attachments = json.loads(row["attachments_json"] or "[]")
                config.smtp_active_profile = str(row["profile_key"] or old_profile or "vesper")
                result = send_email_with_profile(
                    config,
                    [str(r) for r in recipients if str(r).strip()],
                    str(row["subject"] or ""),
                    str(row["body"] or ""),
                    body_html=str(row["body_html"] or ""),
                    attachments=[str(a) for a in attachments if str(a).strip()],
                    include_profile_bcc=True,
                    tracking_id=str(row["tracking_id"] or ""),
                )
                config.smtp_active_profile = old_profile
                if bool(getattr(result, "success", False)):
                    conn.execute(
                        "UPDATE smtp_queue SET status='sent', updated_at=?, last_error='', state_note='ok' WHERE id=?",
                        (time.time(), row_id),
                    )
                    summary.sent += 1
                else:
                    msg = str(getattr(result, "message", "Falha ao reenviar") or "Falha ao reenviar")
                    if attempts >= max_attempts:
                        conn.execute(
                            "UPDATE smtp_queue SET status='failed', updated_at=?, last_error=?, state_note='falha_permanente' WHERE id=?",
                            (time.time(), msg, row_id),
                        )
                    else:
                        delay = _retry_delay_seconds(attempts)
                        conn.execute(
                            "UPDATE smtp_queue SET status='pending', next_attempt_at=?, updated_at=?, last_error=?, state_note='aguardando_retry' WHERE id=?",
                            (time.time() + delay, time.time(), msg, row_id),
                        )
                    summary.failed += 1
            except Exception as exc:
                config.smtp_active_profile = old_profile
                msg = str(exc)
                if attempts >= max_attempts:
                    conn.execute(
                        "UPDATE smtp_queue SET status='failed', updated_at=?, last_error=?, state_note='falha_permanente' WHERE id=?",
                        (time.time(), msg, row_id),
                    )
                else:
                    delay = _retry_delay_seconds(attempts)
                    conn.execute(
                        "UPDATE smtp_queue SET status='pending', next_attempt_at=?, updated_at=?, last_error=?, state_note='aguardando_retry' WHERE id=?",
                        (time.time() + delay, time.time(), msg, row_id),
                    )
                summary.failed += 1
            finally:
                conn.commit()
    summary.pending = pending_count()
    return summary


class SMTPQueueProcessRunnable(QRunnable):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.signals = _QueueSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(process_pending_queue(self.config))
        except Exception as exc:
            self.signals.error.emit(str(exc))
