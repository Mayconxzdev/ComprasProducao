from __future__ import annotations
import sys
from typing import Optional
from .app_log import setup_logging

logger = setup_logging()

def store_smtp_password(username: str, password: str) -> bool:
    """
    Armazena senha SMTP de forma segura usando DPAPI (Windows).

    Args:
        username: Nome de usuário SMTP
        password: Senha a ser armazenada

    Returns:
        True se sucesso, False caso contrário
    """
    try:
        if sys.platform == 'win32':
            # DPAPI Windows
            import win32crypt

            service_name = "ComprasApp_SMTP"
            key = f"{service_name}_{username}"

            # Encrypt password
            encrypted = win32crypt.CryptProtectData(
                password.encode('utf-8'),
                key,
                None,
                None,
                None,
                0
            )

            # Salvar em arquivo local (encrypted)
            from .config import ensure_app_data_dir
            storage_file = ensure_app_data_dir() / ".smtp_cred"
            storage_file.write_bytes(encrypted)

            logger.info(f"Senha SMTP armazenada com DPAPI para {username}")
            return True
        else:
            # Fallback para keyring (cross-platform)
            import keyring
            keyring.set_password("ComprasApp_SMTP", username, password)
            logger.info(f"Senha SMTP armazenada com keyring para {username}")
            return True

    except ImportError as e:
        logger.error(f"Biblioteca necessária não encontrada: {e}")
        logger.error("Instale: pip install pywin32 (Windows) ou pip install keyring")
        return False
    except Exception as e:
        logger.error(f"Erro ao armazenar senha SMTP: {e}")
        return False

def retrieve_smtp_password(username: str) -> Optional[str]:
    """
    Recupera senha SMTP armazenada de forma segura.

    Args:
        username: Nome de usuário SMTP

    Returns:
        Senha descriptografada ou None se não encontrada
    """
    try:
        if sys.platform == 'win32':
            # DPAPI Windows
            import win32crypt
            from .config import ensure_app_data_dir

            storage_file = ensure_app_data_dir() / ".smtp_cred"

            if not storage_file.exists():
                logger.warning("Arquivo de credenciais SMTP não encontrado")
                return None

            encrypted = storage_file.read_bytes()

            # Decrypt
            service_name = "ComprasApp_SMTP"
            key = f"{service_name}_{username}"

            decrypted_data = win32crypt.CryptUnprotectData(
                encrypted,
                None,
                None,
                None,
                0
            )

            password = decrypted_data[1].decode('utf-8')
            logger.info(f"Senha SMTP recuperada com DPAPI para {username}")
            return password
        else:
            # Fallback para keyring
            import keyring
            password = keyring.get_password("ComprasApp_SMTP", username)
            if password:
                logger.info(f"Senha SMTP recuperada com keyring para {username}")
            else:
                logger.warning(f"Senha SMTP não encontrada no keyring para {username}")
            return password

    except ImportError as e:
        logger.error(f"Biblioteca necessária não encontrada: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro ao recuperar senha SMTP: {e}")
        return None

def delete_smtp_password(username: str) -> bool:
    """
    Remove senha SMTP armazenada.

    Args:
        username: Nome de usuário SMTP

    Returns:
        True se sucesso ou não existia, False em caso de erro
    """
    try:
        if sys.platform == 'win32':
            from .config import ensure_app_data_dir
            storage_file = ensure_app_data_dir() / ".smtp_cred"

            if storage_file.exists():
                storage_file.unlink()
                logger.info(f"Senha SMTP removida para {username}")
            return True
        else:
            import keyring
            try:
                keyring.delete_password("ComprasApp_SMTP", username)
                logger.info(f"Senha SMTP removida do keyring para {username}")
            except keyring.errors.PasswordDeleteError:
                # Senha não existia
                pass
            return True

    except Exception as e:
        logger.error(f"Erro ao remover senha SMTP: {e}")
        return False
