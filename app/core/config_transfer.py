"""
Config export/import for easy deployment across multiple PCs
"""
from __future__ import annotations
import json
import base64
from pathlib import Path
from .config import AppConfig, SMTPProfile
from .dpapi_crypto import decrypt_password, encrypt_password


def _b64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _b64_decode(text_b64: str) -> str:
    return base64.b64decode(text_b64.encode("ascii")).decode("utf-8")


def export_config(export_path: str) -> None:
    """
    Exporta configuracao completa para arquivo JSON.
    Formato atual: v2.0 (compatível com AppConfig baseado em smtp_profiles).
    """
    cfg = AppConfig.load()

    smtp_profiles = {}
    for profile_key, profile in cfg.smtp_profiles.items():
        plain_password_b64 = ""
        if profile.shared_password_b64:
            plain_password_b64 = profile.shared_password_b64
        elif profile.password_protected_b64:
            try:
                plain = decrypt_password(profile.password_protected_b64) or ""
                if plain:
                    plain_password_b64 = _b64_encode(plain)
            except Exception:
                plain_password_b64 = ""

        smtp_profiles[profile_key] = {
            "label": profile.label,
            "host": profile.host,
            "port": profile.port,
            "security": profile.security,
            "auth_method": profile.auth_method,
            "username": profile.username,
            "from_email": profile.from_email,
            "bcc_email": profile.bcc_email,
            "timeout_sec": profile.timeout_sec,
            "password_plain_b64": plain_password_b64,
        }

    export_data = {
        "version": "2.0",
        "smtp_active_profile": cfg.smtp_active_profile,
        "smtp_profiles": smtp_profiles,
        "xlsx_sources": cfg.xlsx_sources,
        "xlsx_sheet_name": cfg.xlsx_sheet_name,
        "default_subject_prefix": cfg.default_subject_prefix,
        "export_history_default_dir": cfg.export_history_default_dir,
        "thunderbird_path": cfg.thunderbird_path,
        "nas_master_path": cfg.nas_master_path,
        "pc_hidden_bcc_enabled": bool(cfg.pc_hidden_bcc_enabled),
        "email_signatures": cfg.email_signatures,
        "email_signatures_managed": bool(cfg.email_signatures_managed),
        "web_search": {
            "primary_provider": cfg.web_search.primary_provider,
            "brave_api_key_b64": cfg.web_search.brave_api_key_shared_b64,
            "enable_duckduckgo_search_fallback": bool(getattr(cfg.web_search, "enable_duckduckgo_search_fallback", True)),
            "enable_heavy_fallback": bool(getattr(cfg.web_search, "enable_heavy_fallback", True)),
        },
    }

    export_file = Path(export_path)
    export_file.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")


def import_config(import_path: str) -> tuple[bool, str]:
    """
    Importa configuracao de arquivo JSON.
    Suporta v2.0 (atual) e v1.0 (legado).
    """
    try:
        import_file = Path(import_path)
        if not import_file.exists():
            return False, f"Arquivo nao encontrado: {import_path}"

        data = json.loads(import_file.read_text(encoding="utf-8"))
        version = str(data.get("version", "1.0"))

        cfg = AppConfig.load()

        if version.startswith("2."):
            cfg.smtp_active_profile = data.get("smtp_active_profile", cfg.smtp_active_profile)

            profiles_data = data.get("smtp_profiles", {})
            if isinstance(profiles_data, dict):
                cfg.smtp_profiles = {}
                for key, raw in profiles_data.items():
                    if not isinstance(raw, dict):
                        continue

                    password_protected_b64 = ""
                    plain_b64 = raw.get("password_plain_b64") or ""
                    if plain_b64:
                        try:
                            plain = _b64_decode(plain_b64)
                            if plain:
                                password_protected_b64 = encrypt_password(plain)
                        except Exception:
                            password_protected_b64 = ""

                    cfg.smtp_profiles[key] = SMTPProfile(
                        label=raw.get("label", ""),
                        host=raw.get("host", ""),
                        port=int(raw.get("port", 465)),
                        security=raw.get("security", "ssl"),
                        auth_method=raw.get("auth_method", "password"),
                        username=raw.get("username", ""),
                        from_email=raw.get("from_email", ""),
                        bcc_email=raw.get("bcc_email", ""),
                        timeout_sec=int(raw.get("timeout_sec", 20)),
                        password_protected_b64=password_protected_b64,
                        shared_password_b64=plain_b64 or "",
                    )
        else:
            # Legacy v1 import: map old SMTP fields into profile "producao".
            smtp = data.get("smtp_config", {})
            prod = cfg.smtp_profiles.get("producao") or SMTPProfile()
            prod.host = smtp.get("host", prod.host)
            prod.port = int(smtp.get("port", prod.port))
            prod.security = "starttls" if smtp.get("use_tls", True) else "ssl"
            prod.username = smtp.get("username", prod.username)
            prod.from_email = smtp.get("email_from", prod.from_email)
            prod.bcc_email = smtp.get("bcc_always", prod.bcc_email)

            password_b64 = smtp.get("password_b64")
            if password_b64:
                try:
                    plain = _b64_decode(password_b64)
                    if plain:
                        prod.password_protected_b64 = encrypt_password(plain)
                        prod.shared_password_b64 = password_b64
                except Exception:
                    pass

            cfg.smtp_profiles["producao"] = prod
            cfg.smtp_active_profile = "producao"

        cfg.xlsx_sources = data.get("xlsx_sources", cfg.xlsx_sources)
        cfg.xlsx_sheet_name = data.get("xlsx_sheet_name", cfg.xlsx_sheet_name)
        cfg.default_subject_prefix = data.get("default_subject_prefix", cfg.default_subject_prefix)
        cfg.export_history_default_dir = data.get("export_history_default_dir", cfg.export_history_default_dir)
        cfg.thunderbird_path = data.get("thunderbird_path", cfg.thunderbird_path)
        cfg.nas_master_path = data.get("nas_master_path", cfg.nas_master_path)
        cfg.pc_hidden_bcc_enabled = bool(data.get("pc_hidden_bcc_enabled", cfg.pc_hidden_bcc_enabled))
        if isinstance(data.get("email_signatures"), dict):
            cfg.email_signatures = {
                str(user).lower(): {
                    str(profile_key): str(profile_path)
                    for profile_key, profile_path in profiles.items()
                }
                for user, profiles in data.get("email_signatures", {}).items()
                if isinstance(profiles, dict)
            }
        if "email_signatures_managed" in data:
            cfg.email_signatures_managed = bool(data.get("email_signatures_managed"))
        web_search = data.get("web_search")
        if isinstance(web_search, dict):
            cfg.web_search.set_primary_provider(str(web_search.get("primary_provider") or cfg.web_search.primary_provider))
            raw_b64 = str(web_search.get("brave_api_key_b64") or "")
            if raw_b64:
                cfg.web_search.brave_api_key_shared_b64 = raw_b64
                cfg.web_search.brave_api_key_protected_b64 = ""
            cfg.web_search.enable_duckduckgo_search_fallback = bool(web_search.get("enable_duckduckgo_search_fallback", cfg.web_search.enable_duckduckgo_search_fallback))
            cfg.web_search.enable_heavy_fallback = bool(web_search.get("enable_heavy_fallback", cfg.web_search.enable_heavy_fallback))

        cfg.save()
        return True, "Configuracao importada com sucesso!"

    except json.JSONDecodeError:
        return False, "Arquivo de configuracao invalido (JSON malformado)"
    except Exception as e:
        return False, f"Erro ao importar: {str(e)}"


def get_default_export_filename() -> str:
    """Retorna nome padrao para arquivo de export"""
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"comprasapp_config_{timestamp}.json"
