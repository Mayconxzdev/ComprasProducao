from __future__ import annotations

from app.core import smtp_queue
from app.core.config import AppConfig, IMAPProfile, SMTPProfile
from app.core.config_sync import config_to_master_dict


def test_queue_deduplicates_same_logical_resend(monkeypatch, tmp_path):
    monkeypatch.setattr(smtp_queue, "_db_path", lambda: tmp_path / "queue.sqlite3")
    first = smtp_queue.enqueue_email(
        profile_key="empresa_a",
        recipients=["contato@fornecedor.invalid"],
        subject="Cotação CV-2026-000001",
        body="Solicito cotação.",
        tracking_id="CV-2026-000001",
    )
    second = smtp_queue.enqueue_email(
        profile_key="empresa_a",
        recipients=["contato@fornecedor.invalid"],
        subject="Cotação CV-2026-000001",
        body="Solicito cotação.",
        tracking_id="CV-2026-000001",
    )
    assert first > 0
    assert second == first
    with smtp_queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM smtp_queue").fetchone()[0] == 1


def test_shared_master_config_never_serializes_secrets():
    config = AppConfig()
    config.smtp_profiles = {
        "empresa_a": SMTPProfile(
            username="compras@empresa-a.invalid",
            from_email="compras@empresa-a.invalid",
            password_protected_b64="local-dpapi-value",
            shared_password_b64="base64-is-not-security",
        )
    }
    config.imap_profiles = {
        "empresa_a": IMAPProfile(
            username="compras@empresa-a.invalid",
            password_protected_b64="local-dpapi-value",
            shared_password_b64="base64-is-not-security",
        )
    }
    config.web_search.brave_api_key_protected_b64 = "local-dpapi-value"
    config.web_search.brave_api_key_shared_b64 = "base64-is-not-security"

    payload = config_to_master_dict(config)
    serialized = str(payload)
    assert "password_b64" not in serialized
    assert "base64-is-not-security" not in serialized
    assert "brave_api_key" not in serialized
