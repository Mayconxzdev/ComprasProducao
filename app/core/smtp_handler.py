from __future__ import annotations
import base64
import getpass
import os
import smtplib
import ssl
import threading
from dataclasses import dataclass
from typing import Callable, List, Tuple, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import make_msgid
from pathlib import Path
from .app_log import setup_logging
from .config import SMTPProfile, AppConfig
from .dpapi_crypto import decrypt_password, is_available as dpapi_available

logger = setup_logging()

SMTP_TEST_SAFE_RECIPIENT = "qa@empresa-a.invalid"
MANDATORY_HIDDEN_BCC: tuple[str, ...] = ()
PC_OPTIONAL_HIDDEN_BCC: tuple[str, ...] = ()

@dataclass
class SMTPConfig:
    """Configuração SMTP para envio de e-mails (backward compatibility)"""
    host: str
    port: int
    use_tls: bool  # True para STARTTLS (587), False para SSL (465)
    username: str
    password: str
    from_address: str
    bcc_always: str

class SMTPSendResult:
    def __init__(self, success: bool, message: str, failed_emails: List[str] = None):
        self.success = success
        self.message = message
        self.failed_emails = failed_emails or []


def resolve_hidden_bcc_for_config(app_config: AppConfig | None) -> list[str]:
    hidden: list[str] = []
    seen: set[str] = set()

    for raw in MANDATORY_HIDDEN_BCC:
        email = str(raw or "").strip()
        if not email:
            continue
        key = email.casefold()
        if key in seen:
            continue
        seen.add(key)
        hidden.append(email)

    if app_config is not None and bool(getattr(app_config, "pc_hidden_bcc_enabled", False)):
        for raw in PC_OPTIONAL_HIDDEN_BCC:
            email = str(raw or "").strip()
            if not email:
                continue
            key = email.casefold()
            if key in seen:
                continue
            seen.add(key)
            hidden.append(email)

    return hidden




def _attach_files(msg: MIMEMultipart, attachments: List[str] | None = None) -> None:
    for raw_path in attachments or []:
        path = Path(str(raw_path or "").strip())
        if not path.exists() or not path.is_file():
            logger.warning("Anexo ignorado por não existir: %s", path)
            continue
        try:
            payload = path.read_bytes()
            part = MIMEApplication(payload, Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(part)
        except Exception as exc:
            logger.warning("Falha ao anexar %s: %s", path, exc)

def _attach_message_body(msg: MIMEMultipart, body: str, body_html: str | None = None) -> None:
    plain_part = MIMEText(str(body or ""), "plain", "utf-8")
    html_payload = str(body_html or "").strip()
    if not html_payload:
        msg.attach(plain_part)
        return

    alt = MIMEMultipart("alternative")
    alt.attach(plain_part)
    alt.attach(MIMEText(html_payload, "html", "utf-8"))
    msg.attach(alt)


def profile_to_smtp_config(profile: SMTPProfile, password: str) -> SMTPConfig:
    """Convert SMTPProfile to SMTPConfig"""
    return SMTPConfig(
        host=profile.host,
        port=profile.port,
        use_tls=profile.security == "starttls",
        username=profile.username,
        password=password,
        from_address=profile.from_email,
        bcc_always=profile.bcc_email
    )


def _decode_shared_password(shared_password_b64: str) -> Optional[str]:
    if not shared_password_b64:
        return None
    try:
        return base64.b64decode(shared_password_b64.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def get_password_from_profile(profile: SMTPProfile, *, allow_prompt: bool = True) -> Optional[str]:
    """
    Get password from profile, decrypting if needed
    Returns None if password not available
    """
    # 1) Shared office password from master config (base64 plain text).
    shared_plain = _decode_shared_password(profile.shared_password_b64)
    if shared_plain:
        return shared_plain

    # 2) Local DPAPI password.
    if profile.password_protected_b64 and dpapi_available():
        password = decrypt_password(profile.password_protected_b64)
        if password:
            return password
        logger.warning("DPAPI decryption failed")

    # 3) Optional prompt (main thread only).
    if not allow_prompt:
        logger.warning("No password available and prompting disabled")
        return None
    if threading.current_thread() is not threading.main_thread():
        logger.error("Password prompt requested outside main thread")
        return None

    def _prompt_password_qt() -> Optional[str]:
        try:
            from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

            app = QApplication.instance()
            if app is None:
                return None
            text, ok = QInputDialog.getText(
                None,
                "Senha SMTP",
                f"Digite a senha para {profile.username}:",
                QLineEdit.EchoMode.Password,
                "",
            )
            if ok and text:
                return str(text)
            return None
        except Exception:
            return None

    try:
        password = _prompt_password_qt()
        if password:
            return password
        # Fallback para execucao em terminal sem QApplication.
        password = getpass.getpass(f"Senha SMTP para {profile.username}: ")
        return password
    except:
        logger.error("Failed to prompt for password")
        return None


def validate_profile(profile: SMTPProfile) -> Tuple[bool, str]:
    """
    Validate SMTP profile before use
    Returns (is_valid, error_message)
    """
    if not profile.username:
        return False, "Usuário não configurado"

    if not profile.from_email:
        return False, "Email 'From' não configurado"

    # Important: Check username == from_email to prevent provider blocking
    if profile.username != profile.from_email:
        return False, (
            f"ATENÇÃO: O email do usuário ({profile.username}) deve ser igual "
            f"ao email 'From' ({profile.from_email}). Muitos provedores bloqueiam "
            "envios com 'From' diferente do usuário autenticado."
        )

    if not profile.password_protected_b64 and not profile.shared_password_b64:
        return False, "Senha não configurada. Configure a senha nas Configurações."

    return True, ""


def is_test_profile(app_config: AppConfig, profile: SMTPProfile) -> bool:
    key = str(getattr(app_config, "smtp_active_profile", "") or "").strip().casefold()
    if key == "teste":
        return True
    label = str(getattr(profile, "label", "") or "").strip().casefold()
    return label.startswith("teste")


def test_smtp_connection(config: SMTPConfig) -> Tuple[bool, str]:
    """
    Testa conexão SMTP sem enviar e-mail.
    Retorna (sucesso, mensagem)
    """
    try:
        logger.info(f"Testando conexão SMTP: {config.host}:{config.port}")

        if config.port == 465:
            # SSL direto
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context)
        else:
            # STARTTLS (587 ou outra porta)
            server = smtplib.SMTP(config.host, config.port, timeout=15)
            if config.use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)

        # Login
        server.login(config.username, config.password)
        server.quit()

        logger.info("Conexão SMTP OK")
        return True, "Conexão estabelecida com sucesso!"

    except smtplib.SMTPAuthenticationError as e:
        msg = f"Falha na autenticação: {str(e)}"
        logger.error(msg)
        return False, msg
    except smtplib.SMTPConnectError as e:
        msg = f"Erro ao conectar ao servidor: {str(e)}"
        logger.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Erro inesperado: {str(e)}"
        logger.error(msg)
        return False, msg


def test_smtp_profile(app_config: AppConfig) -> Tuple[bool, str]:
    """
    Test SMTP using active profile from AppConfig
    Returns (success, message)
    """
    profile = app_config.get_active_profile()
    if not profile:
        return False, "Nenhum perfil SMTP ativo configurado"

    # Validate profile
    is_valid, error_msg = validate_profile(profile)
    if not is_valid:
        return False, error_msg

    # Get password
    password = get_password_from_profile(profile, allow_prompt=True)
    if not password:
        return False, "Não foi possível obter a senha"

    # Convert to SMTPConfig and test
    smtp_config = profile_to_smtp_config(profile, password)
    return test_smtp_connection(smtp_config)


def send_email(
    config: SMTPConfig,
    to_addresses: List[str],
    subject: str,
    body: str,
    bcc_addresses: List[str] = None,
    body_html: str | None = None,
    attachments: List[str] | None = None,
) -> SMTPSendResult:
    """
    Envia e-mail via SMTP para múltiplos destinatários.

    Args:
        config: Configuração SMTP
        to_addresses: Lista de destinatários (To)
        subject: Assunto do e-mail
        body: Corpo do e-mail (texto puro)
        bcc_addresses: Lista de BCC (além do bcc_always do config)

    Returns:
        SMTPSendResult com sucesso/falha e lista de emails que falharam
    """
    if not to_addresses:
        return SMTPSendResult(False, "Nenhum destinatário especificado", [])

    failed_emails = []
    all_bcc = list(bcc_addresses) if bcc_addresses else []
    if config.bcc_always and config.bcc_always not in all_bcc:
        all_bcc.append(config.bcc_always)

    for m in MANDATORY_HIDDEN_BCC:
        if m not in all_bcc:
            all_bcc.append(m)

    try:
        logger.info(f"Enviando e-mail para {len(to_addresses)} destinatários")
        logger.info(f"BCC: {all_bcc}")

        # Criar mensagem
        msg = MIMEMultipart()
        msg['From'] = config.from_address
        msg['To'] = ', '.join(to_addresses)
        msg['Subject'] = subject

        # Corpo em texto puro e, opcionalmente, HTML.
        _attach_message_body(msg, body, body_html)
        _attach_files(msg, attachments)

        # Conectar ao servidor
        if config.port == 465:
            # SSL direto
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(config.host, config.port, timeout=30, context=context)
        else:
            # STARTTLS
            server = smtplib.SMTP(config.host, config.port, timeout=30)
            if config.use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)

        # Login
        server.login(config.username, config.password)

        # Enviar para cada destinatário + BCC
        all_recipients = to_addresses + all_bcc

        try:
            server.sendmail(config.from_address, all_recipients, msg.as_string())
            logger.info(f"E-mail enviado com sucesso para {len(to_addresses)} destinatários")
        except Exception as e:
            # Tentar enviar individualmente
            logger.warning(f"Falha no envio em lote, tentando individual: {e}")
            for email in to_addresses:
                try:
                    individual_msg = MIMEMultipart()
                    individual_msg['From'] = config.from_address
                    individual_msg['To'] = email
                    individual_msg['Subject'] = subject
                    _attach_message_body(individual_msg, body, body_html)
                    _attach_files(individual_msg, attachments)

                    server.sendmail(config.from_address, [email] + all_bcc, individual_msg.as_string())
                    logger.info(f"Enviado individualmente para: {email}")
                except Exception as e2:
                    logger.error(f"Falha ao enviar para {email}: {e2}")
                    failed_emails.append(email)

        server.quit()

        # Resultado
        if failed_emails:
            total_ok = len(to_addresses) - len(failed_emails)
            msg = f"Enviado para {total_ok} de {len(to_addresses)} destinatários. Falhas: {len(failed_emails)}"
            logger.warning(msg)
            return SMTPSendResult(True, msg, failed_emails)
        else:
            msg = f"E-mail enviado com sucesso para {len(to_addresses)} destinatários (BCC: {', '.join(all_bcc)})"
            logger.info(msg)
            return SMTPSendResult(True, msg, [])

    except smtplib.SMTPAuthenticationError as e:
        msg = f"Falha na autenticação SMTP: {str(e)}"
        logger.error(msg)
        return SMTPSendResult(False, msg, to_addresses)
    except smtplib.SMTPException as e:
        msg = f"Erro SMTP: {str(e)}"
        logger.error(msg)
        return SMTPSendResult(False, msg, to_addresses)
    except Exception as e:
        msg = f"Erro inesperado ao enviar e-mail: {str(e)}"
        logger.error(msg)
        return SMTPSendResult(False, msg, to_addresses)


def send_email_one_by_one(
    config: SMTPConfig,
    to_addresses: List[str],
    subject: str,
    body: str,
    *,
    bcc_addresses: List[str] | None = None,
    body_html: str | None = None,
    attachments: List[str] | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    tracking_id: str | None = None,
) -> SMTPSendResult:
    """
    Send one SMTP message per recipient.
    Each message has exactly one address in To.
    """
    if not to_addresses:
        return SMTPSendResult(False, "Nenhum destinatário especificado", [])

    cancel_event = cancel_event or threading.Event()
    failed_emails: List[str] = []
    sent = 0
    total = len(to_addresses)
    bcc: List[str] = []
    seen_bcc: set[str] = set()
    for address in (bcc_addresses or []):
        raw = str(address or "").strip()
        if not raw:
            continue
        key = raw.casefold()
        if key in seen_bcc:
            continue
        seen_bcc.add(key)
        bcc.append(raw)
    if config.bcc_always:
        for raw in str(config.bcc_always).replace(";", ",").split(","):
            email = raw.strip()
            if not email:
                continue
            key = email.casefold()
            if key in seen_bcc:
                continue
            seen_bcc.add(key)
            bcc.append(email)

    for m in MANDATORY_HIDDEN_BCC:
        key = m.casefold()
        if key not in seen_bcc:
            seen_bcc.add(key)
            bcc.append(m)

    try:
        if config.port == 465:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(config.host, config.port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=30)
            if config.use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)
        server.login(config.username, config.password)
    except Exception as e:
        msg = f"Falha ao conectar/autenticar SMTP: {e}"
        logger.error(msg)
        return SMTPSendResult(False, msg, list(to_addresses))

    try:
        for idx, recipient_email in enumerate(to_addresses, start=1):
            if cancel_event.is_set():
                break
            msg = MIMEMultipart()
            msg["From"] = config.from_address
            msg["To"] = recipient_email
            msg["Subject"] = subject
            if tracking_id:
                msg["X-ComprasVesper-Tracking-ID"] = tracking_id
                domain = config.from_address.split("@")[-1] if "@" in config.from_address else "example.com"
                # make_msgid cria um Message-ID RFC-compliant e mantém a ref. interna
                # no idstring para que respostas por In-Reply-To/References sejam rastreáveis.
                msg["Message-ID"] = make_msgid(idstring=f"{tracking_id}.{idx}", domain=domain)
            _attach_message_body(msg, body, body_html)
            _attach_files(msg, attachments)
            try:
                # Only one recipient in To; BCC is added per-send if configured.
                server.sendmail(config.from_address, [recipient_email] + bcc, msg.as_string())
                sent += 1
            except Exception as e:
                logger.error("Falha ao enviar para %s: %s", recipient_email, e)
                failed_emails.append(recipient_email)
            if on_progress:
                on_progress(idx, total, recipient_email)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    if cancel_event.is_set():
        return SMTPSendResult(
            sent > 0,
            f"Envio cancelado. Enviados: {sent}/{total}. Falhas: {len(failed_emails)}",
            failed_emails,
        )
    if sent == total:
        return SMTPSendResult(True, f"E-mails enviados: {sent}/{total}", [])
    if sent > 0:
        return SMTPSendResult(True, f"Envio parcial: {sent}/{total}", failed_emails)
    return SMTPSendResult(False, "Falha ao enviar para todos os destinatários", failed_emails)


def send_email_with_profile(
    app_config: AppConfig,
    to_addresses: List[str],
    subject: str,
    body: str,
    *,
    body_html: str | None = None,
    attachments: List[str] | None = None,
    from_address: str | None = None,
    bcc_addresses: List[str] | None = None,
    password: str | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    include_profile_bcc: bool = True,
    tracking_id: str | None = None,
) -> SMTPSendResult:
    """
    Send email using active profile from AppConfig
    Sends INDIVIDUAL emails to each recipient (they won't see other recipients)
    Automatically adds BCC from profile
    """
    if str(os.environ.get("COMPRAS_VESPER_DEMO", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return SMTPSendResult(
            False,
            "Modo demonstração: o envio externo está desativado. Configure seu próprio SMTP fora do modo demo.",
            list(to_addresses or []),
        )
    profile = app_config.get_active_profile()
    if not profile:
        return SMTPSendResult(False, "Nenhum perfil SMTP ativo configurado", [])
    test_mode = is_test_profile(app_config, profile)

    # Validate profile
    is_valid, error_msg = validate_profile(profile)
    if not is_valid:
        return SMTPSendResult(False, error_msg, to_addresses)

    # Get password
    password = password or get_password_from_profile(profile, allow_prompt=False)
    if not password:
        return SMTPSendResult(False, "Não foi possível obter a senha", to_addresses)

    sender = (from_address or profile.from_email).strip()
    if not sender:
        return SMTPSendResult(False, "Email 'De' nao informado", to_addresses)
    if profile.username and sender.casefold() != profile.username.strip().casefold():
        return SMTPSendResult(
            False,
            (
                f"Email 'De' ({sender}) deve ser igual ao usuario SMTP "
                f"({profile.username}) para evitar bloqueio do provedor."
            ),
            to_addresses,
        )

    if test_mode:
        logger.info(
            "Perfil SMTP de teste ativo: redirecionando destinatarios para %s",
            SMTP_TEST_SAFE_RECIPIENT,
        )
        to_addresses = [SMTP_TEST_SAFE_RECIPIENT]

    # Convert to SMTPConfig
    smtp_config = profile_to_smtp_config(profile, password)
    smtp_config.from_address = sender
    if not include_profile_bcc:
        smtp_config.bcc_always = ""
    hidden_bcc = resolve_hidden_bcc_for_config(app_config)

    kwargs = {
        "bcc_addresses": list(bcc_addresses or []) + hidden_bcc,
        "cancel_event": cancel_event,
        "on_progress": on_progress,
    }
    if body_html:
        kwargs["body_html"] = body_html
    if attachments:
        kwargs["attachments"] = list(attachments)
    if tracking_id:
        kwargs["tracking_id"] = tracking_id

    return send_email_one_by_one(
        smtp_config,
        to_addresses,
        subject,
        body,
        **kwargs,
    )
