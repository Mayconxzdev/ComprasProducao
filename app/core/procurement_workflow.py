from __future__ import annotations

import sqlite3
from typing import Any

from .config import ensure_app_data_dir
from .history_store import HistoryStore
from .workflow_state_machine import WorkflowState, apply_transition, can_transition


def _clean(value: Any) -> str:
    return str(value or "").strip()


class ProcurementWorkflowStore:
    def __init__(self, db_path: str | None = None) -> None:
        base = ensure_app_data_dir()
        self._db_path = db_path or str(base / "procurement_workflow.db")
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_state (
                    rfq_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    note TEXT,
                    previous_state TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rfq_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    note TEXT,
                    previous_state TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_audit_rfq ON workflow_audit(rfq_id)")
            conn.commit()
        finally:
            conn.close()

    def get_current_state(self, rfq_id: str) -> str:
        rfq = _clean(rfq_id)
        if not rfq:
            return "requisitada"
        conn = self._connect()
        try:
            row = conn.execute("SELECT state FROM workflow_state WHERE rfq_id = ?", (rfq,)).fetchone()
            return _clean(row["state"]) if row else "requisitada"
        finally:
            conn.close()

    def apply_action(
        self,
        *,
        rfq_id: str,
        action: str,
        role: str,
        actor: str,
        note: str = "",
        history_store: HistoryStore | None = None,
    ) -> WorkflowState:
        rfq = _clean(rfq_id)
        if not rfq:
            raise ValueError("rfq_id obrigatorio")
        current = self.get_current_state(rfq)
        if not can_transition(current, action, role):
            raise ValueError("Transicao nao permitida para o papel atual")
        new_state = apply_transition(current, action, actor, note)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO workflow_state(
                    rfq_id, state, changed_at, actor, action, note, previous_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rfq_id) DO UPDATE SET
                    state=excluded.state,
                    changed_at=excluded.changed_at,
                    actor=excluded.actor,
                    action=excluded.action,
                    note=excluded.note,
                    previous_state=excluded.previous_state
                """,
                (
                    rfq,
                    new_state.state,
                    new_state.changed_at,
                    new_state.actor,
                    new_state.action,
                    new_state.note,
                    new_state.previous_state,
                ),
            )
            conn.execute(
                """
                INSERT INTO workflow_audit(
                    rfq_id, state, changed_at, actor, action, note, previous_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rfq,
                    new_state.state,
                    new_state.changed_at,
                    new_state.actor,
                    new_state.action,
                    new_state.note,
                    new_state.previous_state,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        if history_store is not None:
            history_store.record_send_event(
                status=f"workflow_{new_state.state}",
                product_query=rfq,
                subject="",
                body="",
                recipients=[],
                items=[],
                failed_emails=[],
                event_type="workflow_transition",
                extra={
                    "rfq_id": rfq,
                    "action": new_state.action,
                    "role": _clean(role),
                    "actor": new_state.actor,
                    "note": new_state.note,
                    "previous_state": new_state.previous_state,
                    "state": new_state.state,
                },
            )
        return new_state
