from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_dt(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00"), text.replace("/", "-")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


@dataclass(frozen=True)
class FollowupTask:
    rfq_id: str
    source_event_id: str
    supplier_email: str
    supplier_name: str
    due_at: str
    stage: int
    reason: str


def compute_followup_due(
    events: Iterable[dict[str, Any]],
    now: datetime | None = None,
    schedule_days: tuple[int, int] = (1, 3),
) -> list[FollowupTask]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due_tasks: list[FollowupTask] = []

    followup_sent_keys: set[tuple[str, str, int]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        if _clean(event.get("event_type")) != "followup_sent":
            continue
        extra = event.get("extra") or {}
        if not isinstance(extra, dict):
            continue
        rfq_id = _clean(extra.get("rfq_id"))
        email = _clean(extra.get("recipient_email")).lower()
        stage = int(extra.get("stage") or 0)
        if rfq_id and email and stage:
            followup_sent_keys.add((rfq_id, email, stage))

    for event in events:
        if not isinstance(event, dict):
            continue
        if _clean(event.get("event_type")) != "smtp_send":
            continue
        if _clean(event.get("status")) != "sent_smtp_ok":
            continue
        ts = _parse_dt(event.get("ts"))
        if ts is None:
            continue
        extra = event.get("extra") or {}
        rfq_id = _clean(extra.get("rfq_id")) if isinstance(extra, dict) else ""
        if not rfq_id:
            rfq_id = _clean(event.get("event_id"))
        recipients = event.get("recipients") or []
        if not isinstance(recipients, list):
            continue

        for recipient in recipients:
            if not isinstance(recipient, dict):
                continue
            email = _clean(recipient.get("email")).lower()
            if not email:
                continue
            company = _clean(recipient.get("empresa"))
            for stage, days in enumerate(schedule_days, start=1):
                due_dt = ts + timedelta(days=max(0, int(days)))
                if due_dt > now_utc:
                    continue
                sent_key = (rfq_id, email, stage)
                if sent_key in followup_sent_keys:
                    continue
                due_tasks.append(
                    FollowupTask(
                        rfq_id=rfq_id,
                        source_event_id=_clean(event.get("event_id")),
                        supplier_email=email,
                        supplier_name=company,
                        due_at=due_dt.isoformat(timespec="seconds"),
                        stage=stage,
                        reason=f"Sem resposta após D+{days}",
                    )
                )
    # Guard-rail: one task per supplier/stage.
    seen: set[tuple[str, str, int]] = set()
    unique: list[FollowupTask] = []
    for task in sorted(due_tasks, key=lambda t: (t.rfq_id, t.supplier_email, t.stage)):
        key = (task.rfq_id, task.supplier_email, task.stage)
        if key in seen:
            continue
        seen.add(key)
        unique.append(task)
    return unique
