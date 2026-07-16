from app.core.config import AppConfig, demo_supplier_workbook
from app.core.smtp_handler import send_email_with_profile
from app.core.xlsx_loader import load_suppliers_from_xlsx


def test_demo_mode_is_local_and_network_safe(monkeypatch):
    monkeypatch.setenv("COMPRAS_VESPER_DEMO", "1")
    config = AppConfig.load()
    assert config.xlsx_sources == [str(demo_supplier_workbook())]
    assert config.nas_master_path == ""
    assert config.update_enabled is False
    assert config.email_signatures_managed is True
    assert all(profile.enabled is False for profile in config.imap_profiles.values())


def test_demo_catalog_has_valid_anonymous_suppliers():
    suppliers, warnings = load_suppliers_from_xlsx(str(demo_supplier_workbook()))
    assert warnings == []
    assert len(suppliers) >= 6
    assert all(supplier.is_valid and supplier.email.endswith(".invalid") for supplier in suppliers)


def test_demo_mode_blocks_smtp(monkeypatch):
    monkeypatch.setenv("COMPRAS_VESPER_DEMO", "1")
    result = send_email_with_profile(AppConfig.load(), ["destinatario@exemplo.invalid"], "Teste", "Mensagem")
    assert result.success is False
    assert "Modo demonstração" in result.message
