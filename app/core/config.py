from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Dict, Optional

from .path_utils import normalize_master_path
from .dpapi_crypto import decrypt_password, encrypt_password

APP_NAME = "ComprasApp"

DEFAULT_FEATURE_FLAGS: Dict[str, bool] = {
    # A UX final prioriza envio rápido. Recursos pesados ficam desligados por padrão.
    "supplier_operational_score": False,
    "supplier_recommendations": False,
    "email_template_suggestions": True,
    "followup_scheduler": False,
    "procurement_workflow": False,
    "quote_chat_assistant": False,
}

CANONICAL_SMTP_PROFILE_LABELS: Dict[str, str] = {
    "vesper": "Empresa A",
    "ventrio": "Empresa B",
    "producao": "Empresa B",  # compatibilidade com instalações antigas
    "teste": "Teste local",
}


def demo_mode_enabled() -> bool:
    return str(os.environ.get("COMPRAS_VESPER_DEMO", "")).strip().lower() in {"1", "true", "yes", "on"}


def demo_supplier_workbook() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "fornecedores-demo.xlsx"


def canonical_smtp_profile_label(profile_key: str, current_label: str = "") -> str:
    key = str(profile_key or "").strip()
    if key in CANONICAL_SMTP_PROFILE_LABELS:
        return CANONICAL_SMTP_PROFILE_LABELS[key]
    label = str(current_label or "").strip()
    return label or key

@dataclass
class SMTPProfile:
    """SMTP profile configuration"""
    label: str = "Produção"
    host: str = "smtp.example.com"
    port: int = 465
    security: str = "ssl"  # "ssl" or "starttls"
    auth_method: str = "password"
    username: str = ""
    from_email: str = ""
    bcc_email: str = ""
    timeout_sec: int = 20
    password_protected_b64: str = ""  # DPAPI encrypted password in base64
    shared_password_b64: str = ""  # campo legado: nunca deve conter segredo




@dataclass
class IMAPProfile:
    """IMAP profile used to read real supplier replies."""
    label: str = "Empresa A"
    host: str = "imap.example.com"
    port: int = 993
    security: str = "ssl"  # currently SSL/TLS on 993
    username: str = ""
    enabled: bool = True
    mailbox: str = "INBOX"
    timeout_sec: int = 20
    password_protected_b64: str = ""
    shared_password_b64: str = ""  # campo legado: nunca deve conter segredo

@dataclass
class WebSearchConfig:
    """Global web-search provider settings."""
    primary_provider: str = "disabled"  # busca web removida do fluxo comum
    brave_api_key_protected_b64: str = ""
    brave_api_key_shared_b64: str = ""  # campo legado: nunca deve conter segredo
    enable_duckduckgo_search_fallback: bool = False
    enable_heavy_fallback: bool = False

    def _provider_norm(self) -> str:
        p = (self.primary_provider or "").strip().lower()
        if p in {"disabled", "brave", "auto", "google"}:
            return p
        return "disabled"

    def set_primary_provider(self, provider: str) -> None:
        self.primary_provider = provider
        self.primary_provider = self._provider_norm()

    def set_brave_api_key(self, api_key: str) -> None:
        key = (api_key or "").strip()
        if not key:
            self.brave_api_key_protected_b64 = ""
            self.brave_api_key_shared_b64 = ""
            return
        try:
            self.brave_api_key_protected_b64 = encrypt_password(key)
        except Exception:
            self.brave_api_key_protected_b64 = ""

    def clear_brave_api_key(self) -> None:
        self.brave_api_key_protected_b64 = ""
        self.brave_api_key_shared_b64 = ""

    def get_brave_api_key(self) -> str:
        if self.brave_api_key_protected_b64:
            try:
                plain = decrypt_password(self.brave_api_key_protected_b64) or ""
                if plain:
                    return plain.strip()
            except Exception:
                pass
        return ""

    def masked_brave_api_key(self) -> str:
        key = self.get_brave_api_key()
        if not key:
            return ""
        if len(key) <= 7:
            return key[:2] + "..." if len(key) > 2 else "***"
        return f"{key[:4]}...{key[-2:]}"


def app_data_dir() -> Path:
    # Windows: %APPDATA%\ComprasApp
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    # fallback for non-windows environments
    return Path.home() / f".{APP_NAME.lower()}"

def ensure_app_data_dir() -> Path:
    d = app_data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        # verify we can actually write there
        probe = d / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return d
    except (PermissionError, OSError):
        # Fallback for restricted environments (e.g., sandboxed runs)
        fallback = Path.cwd() / ".appdata"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

def config_path() -> Path:
    return ensure_app_data_dir() / "config.json"

@dataclass
class AppConfig:
    xlsx_sources: List[str] = field(default_factory=list)
    nas_master_path: str = ""
    export_history_default_dir: str = ""
    thunderbird_path: str = ""  # Manter como fallback opcional
    default_subject_prefix: str = "Cotação"

    # SMTP Profiles (NEW)
    smtp_active_profile: str = "vesper"  # "vesper" or "ventrio" no uso comum
    smtp_profiles: Dict[str, SMTPProfile] = field(default_factory=dict)
    imap_profiles: Dict[str, IMAPProfile] = field(default_factory=dict)
    imap_check_on_open_history: bool = False
    # Operacional: ao instalar esta versao, historico antigo fica oculto do Acompanhar por padrao.
    # Isso evita que testes/legados anteriores ao IMAP pareçam pendencia real.
    hide_pre_tracking_history: bool = True
    history_tracking_cutover_at: str = ""
    history_export_on_send: bool = False
    default_company_key: str = "vesper"
    default_signature_owner: str = ""
    last_signature_owner: str = ""
    signature_auto_map: Dict[str, str] = field(default_factory=dict)

    # XLSX
    xlsx_sheet_name: str = "Fornecedores"  # Nome da aba customizável
    # Search history
    search_history: list[str] = field(default_factory=list)  # Últimas 10 buscas
    # Email Signatures mapping: username -> {profile -> path}
    email_signatures: Dict[str, Dict[str, str]] = field(default_factory=dict)
    email_signatures_managed: bool = False
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    update_enabled: bool = False
    update_source_path: str = ""
    update_channel: str = "stable"
    update_check_on_start: bool = False
    update_download_silent: bool = True
    update_prompt_restart: bool = True
    ui_scale_mode: str = "auto"
    pc_hidden_bcc_enabled: bool = False
    feature_flags: Dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_FEATURE_FLAGS))
    custom_quote_types: List[Dict[str, object]] = field(default_factory=list)

    def __post_init__(self):
        """Initialize default profiles if empty"""
        if not self.smtp_profiles:
            self.smtp_profiles = self._create_default_profiles()
        else:
            defaults = self._create_default_profiles()
            # Migra instalações antigas para os dois perfis reais do uso comum.
            legacy_prod = self.smtp_profiles.get("producao")
            if legacy_prod is not None and "ventrio" not in self.smtp_profiles:
                self.smtp_profiles["ventrio"] = legacy_prod
            for key in ("vesper", "ventrio"):
                self.smtp_profiles.setdefault(key, defaults[key])
            if legacy_prod is not None:
                ventrio = self.smtp_profiles.get("ventrio")
                if ventrio is not None:
                    if not getattr(ventrio, "password_protected_b64", ""):
                        ventrio.password_protected_b64 = getattr(legacy_prod, "password_protected_b64", "")
        if self.smtp_active_profile == "producao":
            self.smtp_active_profile = "ventrio"
        self._align_smtp_transport_from_test()
        self.normalize_smtp_profile_labels()
        self._enforce_mandatory_bcc()
        self.ensure_imap_profiles()
        self.history_tracking_cutover_at = str(self.history_tracking_cutover_at or "").strip()
        if not self.history_tracking_cutover_at:
            self.history_tracking_cutover_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.hide_pre_tracking_history = self._to_bool(self.hide_pre_tracking_history, True)
        if not isinstance(self.web_search, WebSearchConfig):
            self.web_search = WebSearchConfig()
        self.web_search.set_primary_provider(self.web_search.primary_provider)
        self.nas_master_path = normalize_master_path(self.nas_master_path)
        self.update_source_path = normalize_master_path(self.update_source_path)
        self.update_channel = str(self.update_channel or "stable").strip().lower() or "stable"
        self.ui_scale_mode = str(self.ui_scale_mode or "auto").strip().lower() or "auto"
        if not isinstance(self.feature_flags, dict):
            self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        else:
            merged = dict(DEFAULT_FEATURE_FLAGS)
            for key, value in self.feature_flags.items():
                merged[str(key)] = bool(value)
            self.feature_flags = merged

    def _enforce_mandatory_bcc(self) -> None:
        mandatory: list[str] = []
        for profile in self.smtp_profiles.values():
            current = str(profile.bcc_email or "")
            bcc_list = []
            for e in current.replace(";", ",").split(","):
                email = e.strip()
                if email and email not in bcc_list:
                    bcc_list.append(email)
            for m in mandatory:
                if m not in bcc_list:
                    bcc_list.append(m)
            profile.bcc_email = ";".join(bcc_list)

    @staticmethod
    def _to_bool(value: object, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    @staticmethod
    def _create_default_profiles() -> Dict[str, SMTPProfile]:
        """Create default production and test SMTP profiles"""
        return {
            "vesper": SMTPProfile(
                label="Empresa A",
                host="smtp.example.com",
                port=465,
                security="ssl",
                auth_method="password",
                username="compras@empresa-a.invalid",
                from_email="compras@empresa-a.invalid",
                bcc_email="",
                timeout_sec=20,
                password_protected_b64=""
            ),
            "ventrio": SMTPProfile(
                label="Empresa B",
                host="smtp.example.com",
                port=465,
                security="ssl",
                auth_method="password",
                username="compras@empresa-b.invalid",
                from_email="compras@empresa-b.invalid",
                bcc_email="",
                timeout_sec=20,
                password_protected_b64=""
            ),
            "teste": SMTPProfile(
                label="Teste local",
                host="smtp.example.com",
                port=465,
                security="ssl",
                auth_method="password",
                username="qa@empresa-a.invalid",
                from_email="qa@empresa-a.invalid",
                bcc_email="",
                timeout_sec=20,
                password_protected_b64=""
            )
        }


    @staticmethod
    def _create_default_imap_profiles() -> Dict[str, IMAPProfile]:
        return {
            "vesper": IMAPProfile(
                label="Empresa A",
                host="imap.example.com",
                port=993,
                security="ssl",
                username="compras@empresa-a.invalid",
                enabled=False,
                mailbox="INBOX",
            ),
            "ventrio": IMAPProfile(
                label="Empresa B",
                host="imap.example.com",
                port=993,
                security="ssl",
                username="compras@empresa-b.invalid",
                enabled=False,
                mailbox="INBOX",
            ),
        }

    def ensure_imap_profiles(self) -> None:
        defaults = self._create_default_imap_profiles()
        if not isinstance(self.imap_profiles, dict) or not self.imap_profiles:
            self.imap_profiles = defaults
            return
        for key, profile in defaults.items():
            self.imap_profiles.setdefault(key, profile)
        for key, profile in list(self.imap_profiles.items()):
            if not isinstance(profile, IMAPProfile):
                continue
            profile.label = str(profile.label or defaults.get(key, profile).label or key).strip()
            profile.host = str(profile.host or "imap.example.com").strip()
            try:
                profile.port = int(profile.port or 993)
            except Exception:
                profile.port = 993
            profile.security = "ssl"
            profile.mailbox = str(profile.mailbox or "INBOX").strip() or "INBOX"
            profile.username = str(profile.username or defaults.get(key, profile).username or "").strip()
            profile.enabled = bool(profile.enabled)

    @staticmethod
    def _normalize_smtp_security(security: str) -> str:
        s = str(security or "").strip().lower()
        return "starttls" if s == "starttls" else "ssl"

    @staticmethod
    def _normalize_smtp_port(port: object) -> int:
        try:
            value = int(port)  # type: ignore[arg-type]
            return value if value > 0 else 465
        except Exception:
            return 465

    def apply_smtp_transport_to_all_profiles(self, host: str, port: object, security: str) -> None:
        """Keep host/port/security identical across all SMTP profiles."""
        if not self.smtp_profiles:
            self.smtp_profiles = self._create_default_profiles()
        host_norm = str(host or "").strip() or "smtp.example.com"
        port_norm = self._normalize_smtp_port(port)
        security_norm = self._normalize_smtp_security(security)
        for profile in self.smtp_profiles.values():
            profile.host = host_norm
            profile.port = port_norm
            profile.security = security_norm

    def _align_smtp_transport_from_test(self) -> None:
        """Use 'teste' profile as transport template for all profiles."""
        if not self.smtp_profiles:
            return
        template = self.smtp_profiles.get("teste")
        if template is None:
            template = next(iter(self.smtp_profiles.values()))
        self.apply_smtp_transport_to_all_profiles(
            template.host,
            template.port,
            template.security,
        )

    def normalize_smtp_profile_labels(self) -> None:
        """Keep canonical labels for built-in SMTP profile keys."""
        if not self.smtp_profiles:
            return
        for key, profile in self.smtp_profiles.items():
            profile.label = canonical_smtp_profile_label(key, getattr(profile, "label", ""))

    def get_active_profile(self) -> Optional[SMTPProfile]:
        """Get the currently active SMTP profile"""
        profile = self.smtp_profiles.get(self.smtp_active_profile)
        if profile is not None:
            return profile
        if self.smtp_profiles:
            self.smtp_active_profile = next(iter(self.smtp_profiles.keys()))
            return self.smtp_profiles.get(self.smtp_active_profile)
        return None

    def get_brave_api_key(self) -> str:
        return self.web_search.get_brave_api_key() if self.web_search else ""

    def is_feature_enabled(self, key: str, default: bool = False) -> bool:
        if not key:
            return bool(default)
        if not isinstance(self.feature_flags, dict):
            return bool(default)
        if key in self.feature_flags:
            return bool(self.feature_flags.get(key))
        return bool(DEFAULT_FEATURE_FLAGS.get(key, default))

    def set_feature_flag(self, key: str, enabled: bool) -> None:
        if not key:
            return
        if not isinstance(self.feature_flags, dict):
            self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        self.feature_flags[str(key)] = bool(enabled)

    @classmethod
    def load(cls) -> "AppConfig":
        if demo_mode_enabled():
            cfg = cls()
            workbook = demo_supplier_workbook()
            cfg.xlsx_sources = [str(workbook)] if workbook.exists() else []
            cfg.nas_master_path = ""
            cfg.export_history_default_dir = ""
            cfg.update_enabled = False
            cfg.update_check_on_start = False
            cfg.update_source_path = ""
            cfg.email_signatures = {}
            cfg.email_signatures_managed = True
            cfg.signature_auto_map = {}
            cfg.imap_check_on_open_history = False
            cfg.__post_init__()
            for profile in cfg.imap_profiles.values():
                profile.enabled = False
            return cfg
        p = config_path()
        if not p.exists():
            cfg = cls()
            cfg.__post_init__()  # Ensure defaults
            return cfg
        try:
            # Accept UTF-8 with or without BOM.
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            cfg = cls()
            cfg.__post_init__()
            return cfg

        # Create instance with defaults
        cfg = cls()

        # Load basic fields
        for k in ['xlsx_sources', 'export_history_default_dir', 'thunderbird_path',
                  'default_subject_prefix', 'xlsx_sheet_name', 'search_history',
                  'smtp_active_profile', 'nas_master_path', 'update_source_path',
                  'update_channel', 'ui_scale_mode', 'email_signatures',
                  'email_signatures_managed', 'default_company_key',
                  'default_signature_owner', 'last_signature_owner',
                  'signature_auto_map', 'imap_check_on_open_history',
                  'hide_pre_tracking_history', 'history_tracking_cutover_at',
                  'history_export_on_send', 'custom_quote_types']:
            if k in data:
                setattr(cfg, k, data[k])
        cfg.update_enabled = cls._to_bool(data.get("update_enabled", cfg.update_enabled), cfg.update_enabled)
        cfg.update_check_on_start = cls._to_bool(
            data.get("update_check_on_start", cfg.update_check_on_start),
            cfg.update_check_on_start,
        )
        cfg.update_download_silent = cls._to_bool(
            data.get("update_download_silent", cfg.update_download_silent),
            cfg.update_download_silent,
        )
        cfg.update_prompt_restart = cls._to_bool(
            data.get("update_prompt_restart", cfg.update_prompt_restart),
            cfg.update_prompt_restart,
        )
        cfg.pc_hidden_bcc_enabled = cls._to_bool(
            data.get("pc_hidden_bcc_enabled", cfg.pc_hidden_bcc_enabled),
            cfg.pc_hidden_bcc_enabled,
        )
        cfg.hide_pre_tracking_history = cls._to_bool(
            data.get("hide_pre_tracking_history", cfg.hide_pre_tracking_history),
            True,
        )
        cfg.history_export_on_send = cls._to_bool(
            data.get("history_export_on_send", cfg.history_export_on_send),
            False,
        )
        cfg.history_tracking_cutover_at = str(
            data.get("history_tracking_cutover_at", cfg.history_tracking_cutover_at) or ""
        ).strip() or time.strftime("%Y-%m-%dT%H:%M:%S")
        raw_flags = data.get("feature_flags")
        if isinstance(raw_flags, dict):
            cfg.feature_flags = {str(k): bool(v) for k, v in raw_flags.items()}

        web_raw = data.get("web_search")
        if isinstance(web_raw, dict):
            ws = WebSearchConfig()
            ws.set_primary_provider(str(web_raw.get("primary_provider") or ws.primary_provider))
            ws.brave_api_key_protected_b64 = str(web_raw.get("brave_api_key_protected_b64") or "")
            # Nunca reidratar chave de API em Base64: isso não é criptografia.
            ws.brave_api_key_shared_b64 = ""
            ws.enable_duckduckgo_search_fallback = bool(web_raw.get("enable_duckduckgo_search_fallback", ws.enable_duckduckgo_search_fallback))
            ws.enable_heavy_fallback = bool(web_raw.get("enable_heavy_fallback", ws.enable_heavy_fallback))
            cfg.web_search = ws
        else:
            # Busca web foi removida do fluxo comum; mantém compatibilidade, mas desligada.
            cfg.web_search = WebSearchConfig()
        # Load SMTP profiles (with migration from old format)
        if 'smtp_profiles' in data and isinstance(data['smtp_profiles'], dict):
            # New format - load profiles
            cfg.smtp_profiles = {}
            for profile_name, profile_data in data['smtp_profiles'].items():
                if isinstance(profile_data, dict):
                    row = dict(profile_data)
                    # Segredos de arquivos legados/compartilhados são descartados.
                    row.pop("password_b64", None)
                    row["shared_password_b64"] = ""
                    cfg.smtp_profiles[profile_name] = SMTPProfile(**row)
        else:
            # Old format - migrate to new profiles
            cfg.smtp_profiles = cfg._create_default_profiles()

            # Migrate old smtp_host/smtp_username if they exist
            if 'smtp_host' in data and data.get('smtp_host'):
                # Update production profile with old values
                prod = cfg.smtp_profiles.get('ventrio') or cfg.smtp_profiles.get('producao') or next(iter(cfg.smtp_profiles.values()))
                prod.host = data.get('smtp_host', prod.host)
                prod.port = data.get('smtp_port', prod.port)
                prod.username = data.get('smtp_username', prod.username)
                if 'email_from' in data:
                    prod.from_email = data['email_from']
                if 'bcc_always' in data:
                    prod.bcc_email = data['bcc_always']
                # Set security based on smtp_use_tls
                prod.security = "starttls" if data.get('smtp_use_tls', True) else "ssl"


        if 'imap_profiles' in data and isinstance(data['imap_profiles'], dict):
            cfg.imap_profiles = {}
            for profile_name, profile_data in data['imap_profiles'].items():
                if isinstance(profile_data, dict):
                    row = dict(profile_data)
                    row.pop("password_b64", None)
                    row["shared_password_b64"] = ""
                    cfg.imap_profiles[profile_name] = IMAPProfile(**row)
        else:
            cfg.imap_profiles = cfg._create_default_imap_profiles()

        # Ensure profiles exist and migrate common profiles
        if not cfg.smtp_profiles:
            cfg.smtp_profiles = cfg._create_default_profiles()
        else:
            defaults = cfg._create_default_profiles()
            legacy_prod = cfg.smtp_profiles.get("producao")
            if legacy_prod is not None and "ventrio" not in cfg.smtp_profiles:
                cfg.smtp_profiles["ventrio"] = legacy_prod
            cfg.smtp_profiles.setdefault("vesper", defaults["vesper"])
            cfg.smtp_profiles.setdefault("ventrio", defaults["ventrio"])
            cfg.smtp_profiles.setdefault("teste", defaults["teste"])
            if legacy_prod is not None:
                ventrio = cfg.smtp_profiles.get("ventrio")
                if ventrio is not None:
                    if not getattr(ventrio, "password_protected_b64", ""):
                        ventrio.password_protected_b64 = getattr(legacy_prod, "password_protected_b64", "")
        if cfg.smtp_active_profile == "producao":
            cfg.smtp_active_profile = "ventrio"
        if cfg.smtp_active_profile not in cfg.smtp_profiles:
            cfg.smtp_active_profile = "vesper"

        # Sanitize
        if not isinstance(cfg.xlsx_sources, list):
            cfg.xlsx_sources = []
        if not getattr(cfg, "email_signatures", None) or not isinstance(cfg.email_signatures, dict):
            cfg.email_signatures = {}
        else:
            clean_sigs = {}
            for k, v in cfg.email_signatures.items():
                if isinstance(v, dict):
                    clean_sigs[str(k).lower()] = {str(pk): str(pv) for pk, pv in v.items()}
            cfg.email_signatures = clean_sigs
        if "email_signatures" in data and "email_signatures_managed" not in data:
            cfg.email_signatures_managed = True
        cfg.nas_master_path = normalize_master_path(cfg.nas_master_path)
        cfg.update_source_path = normalize_master_path(cfg.update_source_path)
        cfg.update_channel = str(cfg.update_channel or "stable").strip().lower() or "stable"
        cfg.ui_scale_mode = str(cfg.ui_scale_mode or "auto").strip().lower() or "auto"
        if not isinstance(cfg.web_search, WebSearchConfig):
            cfg.web_search = WebSearchConfig()
        cfg.web_search.set_primary_provider("disabled")
        cfg.web_search.brave_api_key_shared_b64 = ""
        cfg.web_search.enable_duckduckgo_search_fallback = False
        cfg.web_search.enable_heavy_fallback = False
        cfg.default_company_key = str(getattr(cfg, "default_company_key", "vesper") or "vesper").strip().lower() or "vesper"
        if cfg.default_company_key not in {"vesper", "ventrio"}:
            cfg.default_company_key = "vesper"
        if not isinstance(getattr(cfg, "signature_auto_map", {}), dict):
            cfg.signature_auto_map = {}
        else:
            cfg.signature_auto_map = {str(k).strip().lower(): str(v).strip().lower() for k, v in cfg.signature_auto_map.items() if str(k).strip() and str(v).strip()}
        if not isinstance(cfg.feature_flags, dict):
            cfg.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        else:
            merged = dict(DEFAULT_FEATURE_FLAGS)
            for key, value in cfg.feature_flags.items():
                merged[str(key)] = bool(value)
            cfg.feature_flags = merged
        cfg._align_smtp_transport_from_test()
        cfg.normalize_smtp_profile_labels()
        cfg._enforce_mandatory_bcc()
        cfg.ensure_imap_profiles()
        cfg.hide_pre_tracking_history = cls._to_bool(cfg.hide_pre_tracking_history, True)
        cfg.history_tracking_cutover_at = str(cfg.history_tracking_cutover_at or "").strip() or time.strftime("%Y-%m-%dT%H:%M:%S")
        if not isinstance(getattr(cfg, "custom_quote_types", []), list):
            cfg.custom_quote_types = []
        else:
            cleaned_types = []
            for item in cfg.custom_quote_types:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                type_id = str(item.get("id") or name.lower().replace(" ", "_")).strip()
                raw_fields = item.get("fields") if isinstance(item.get("fields"), list) else []
                fields = []
                for field_item in raw_fields[:8]:
                    if not isinstance(field_item, dict):
                        continue
                    field_label = str(field_item.get("label") or "").strip()[:36]
                    if not field_label:
                        continue
                    var = str(field_item.get("var") or field_label.upper()).strip().upper()
                    var = "".join(ch if ch.isalnum() else "_" for ch in var).strip("_")[:32] or "CAMPO"
                    fields.append({
                        "label": field_label,
                        "var": var,
                        "required": cls._to_bool(field_item.get("required", True), True),
                        "multiline": cls._to_bool(field_item.get("multiline", False), False),
                        "placeholder": str(field_item.get("placeholder") or "").strip()[:80],
                    })
                if not fields:
                    fields = [{"label": "Conteúdo", "var": "CONTEUDO", "required": True, "multiline": True, "placeholder": "Digite o que será enviado."}]
                cleaned_types.append({
                    "id": type_id,
                    "name": name[:48],
                    "description": str(item.get("description") or "Envio personalizado.").strip()[:120],
                    "icon": str(item.get("icon") or "material").strip(),
                    "color": str(item.get("color") or "blue").strip(),
                    "subject_template": str(item.get("subject_template") or "{EMPRESA} <> {TIPO} <> {TITULO}"),
                    "body_template": str(item.get("body_template") or "Prezados,\n\nSolicito cotação conforme abaixo:\n\n{CONTEUDO}\n\nFico no aguardo.\n\n{ASSINATURA}"),
                    "followup_template": str(item.get("followup_template") or "Prezados,\n\nPoderiam, por gentileza, nos retornar sobre {TIPO}?\n\n{ASSUNTO}\n\nFico no aguardo."),
                    "fields": fields,
                    "active": bool(item.get("active", True)),
                })
            cfg.custom_quote_types = cleaned_types[:24]

        return cfg

    def save(self) -> None:
        """Save configuration to JSON"""
        p = config_path()
        self._align_smtp_transport_from_test()
        self.ensure_imap_profiles()
        for profile in self.smtp_profiles.values():
            profile.shared_password_b64 = ""
        for profile in self.imap_profiles.values():
            profile.shared_password_b64 = ""
        self.web_search.brave_api_key_shared_b64 = ""

        # Convert to dict with proper profile serialization
        data = {
            'xlsx_sources': self.xlsx_sources,
            'export_history_default_dir': self.export_history_default_dir,
            'thunderbird_path': self.thunderbird_path,
            'default_subject_prefix': self.default_subject_prefix,
            'nas_master_path': normalize_master_path(self.nas_master_path),
            'update_enabled': bool(self.update_enabled),
            'update_source_path': normalize_master_path(self.update_source_path),
            'update_channel': str(self.update_channel or "stable").strip().lower() or "stable",
            'update_check_on_start': bool(self.update_check_on_start),
            'update_download_silent': bool(self.update_download_silent),
            'update_prompt_restart': bool(self.update_prompt_restart),
            'ui_scale_mode': str(self.ui_scale_mode or "auto").strip().lower() or "auto",
            'pc_hidden_bcc_enabled': bool(self.pc_hidden_bcc_enabled),
            'default_company_key': str(self.default_company_key or "vesper").strip().lower() or "vesper",
            'default_signature_owner': str(self.default_signature_owner or ""),
            'last_signature_owner': str(self.last_signature_owner or ""),
            'signature_auto_map': dict(self.signature_auto_map or {}),
            'smtp_active_profile': self.smtp_active_profile,
            'smtp_profiles': {
                name: asdict(profile)
                for name, profile in self.smtp_profiles.items()
            },
            'imap_profiles': {
                name: asdict(profile)
                for name, profile in self.imap_profiles.items()
            },
            'imap_check_on_open_history': bool(self.imap_check_on_open_history),
            'hide_pre_tracking_history': bool(self.hide_pre_tracking_history),
            'history_tracking_cutover_at': str(self.history_tracking_cutover_at or ''),
            'history_export_on_send': bool(getattr(self, 'history_export_on_send', False)),
            'xlsx_sheet_name': self.xlsx_sheet_name,
            'search_history': self.search_history,
            'web_search': {**asdict(self.web_search), "primary_provider": "disabled", "enable_duckduckgo_search_fallback": False, "enable_heavy_fallback": False},
            'feature_flags': self.feature_flags,
            'custom_quote_types': list(self.custom_quote_types or []),
            'email_signatures': self.email_signatures,
            'email_signatures_managed': bool(self.email_signatures_managed),
        }

        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_search_to_history(self, search_term: str) -> None:
        """Adiciona termo de busca ao histórico (máx 10)"""
        search_term = search_term.strip()
        if not search_term:
            return

        # Remover duplicatas (mover para o topo se já existe)
        if search_term in self.search_history:
            self.search_history.remove(search_term)

        # Adicionar no início
        self.search_history.insert(0, search_term)

        # Limitar a 10
        self.search_history = self.search_history[:10]

        # Salvar
        self.save()
