from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


VALID_STATES = {
    "requisitada",
    "em_aprovacao",
    "aprovada",
    "ordem_emitida",
    "cancelada",
}


TRANSITIONS: dict[str, dict[str, str]] = {
    "requisitada": {
        "submit_approval": "em_aprovacao",
        "cancel": "cancelada",
    },
    "em_aprovacao": {
        "approve": "aprovada",
        "reject": "requisitada",
        "cancel": "cancelada",
    },
    "aprovada": {
        "issue_order": "ordem_emitida",
        "cancel": "cancelada",
    },
    "ordem_emitida": {},
    "cancelada": {},
}


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "requester": {"submit_approval", "cancel"},
    "approver": {"approve", "reject", "cancel"},
    "buyer": {"issue_order", "cancel"},
    "admin": {"submit_approval", "approve", "reject", "issue_order", "cancel"},
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class WorkflowState:
    state: str
    changed_at: str
    actor: str
    action: str
    note: str = ""
    previous_state: str = ""


def can_transition(current_state: str, action: str, role: str) -> bool:
    state_key = _clean(current_state).lower() or "requisitada"
    action_key = _clean(action).lower()
    role_key = _clean(role).lower() or "requester"
    if state_key not in VALID_STATES:
        return False
    allowed_actions = ROLE_PERMISSIONS.get(role_key, set())
    if action_key not in allowed_actions:
        return False
    next_state = TRANSITIONS.get(state_key, {}).get(action_key)
    return bool(next_state)


def apply_transition(current_state: str, action: str, actor: str, note: str) -> WorkflowState:
    state_key = _clean(current_state).lower() or "requisitada"
    action_key = _clean(action).lower()
    next_state = _clean(TRANSITIONS.get(state_key, {}).get(action_key)).lower()
    if not next_state:
        raise ValueError(f"Transicao invalida: {state_key} + {action_key}")
    note_text = _clean(note)
    if action_key in {"reject", "cancel"} and not note_text:
        raise ValueError("Comentario obrigatorio para rejeitar/cancelar")
    return WorkflowState(
        state=next_state,
        changed_at=_utc_now(),
        actor=_clean(actor) or "usuario",
        action=action_key,
        note=note_text,
        previous_state=state_key,
    )
