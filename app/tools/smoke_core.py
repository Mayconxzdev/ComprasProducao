from __future__ import annotations
from app.core.config import AppConfig
from app.core.config_sync import sync_from_master
from app.core.history_store import HistoryStore


def main() -> int:
    cfg = AppConfig.load()
    ok_sync, msg_sync = sync_from_master(cfg)
    print(f"sync_ok={ok_sync} msg={msg_sync}")

    store = HistoryStore(cfg)
    ok_hist, msg_hist, event_id = store.record_send_event(
        status="smoke_test",
        product_query="smoke",
        subject="smoke",
        body="smoke",
        recipients=[],
        items=[],
        failed_emails=[],
        event_type="smoke",
        extra={"from": "smoke_core"},
    )
    print(f"history_ok={ok_hist} msg={msg_hist} event_id={event_id}")

    # Success if event was persisted globally or queued locally.
    if ok_hist or "outbox" in msg_hist.lower() or "sem_global" in msg_hist.lower():
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
