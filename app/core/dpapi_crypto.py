"""
DPAPI Password Encryption for Windows
Encrypts passwords using Windows Data Protection API
"""
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import win32crypt
    DPAPI_AVAILABLE = True
except ImportError:
    DPAPI_AVAILABLE = False
    logger.warning("win32crypt not available - DPAPI encryption disabled")


def encrypt_password(plain_text: str) -> str:
    """
    Encrypt password using DPAPI (Windows only)

    Args:
        plain_text: Password in plain text

    Returns:
        Base64-encoded encrypted password

    Raises:
        RuntimeError: If DPAPI not available or encryption fails
    """
    if not DPAPI_AVAILABLE:
        raise RuntimeError("DPAPI not available on this system")

    if not plain_text:
        return ""

    try:
        # Encrypt using DPAPI
        encrypted_bytes = win32crypt.CryptProtectData(
            plain_text.encode('utf-8'),
            None,  # Optional entropy
            None,  # Reserved
            None,  # Reserved
            None,  # Prompt struct
            0      # Flags
        )

        # Encode to base64 for storage
        return base64.b64encode(encrypted_bytes).decode('ascii')

    except Exception as e:
        logger.error(f"Failed to encrypt password: {e}")
        raise RuntimeError(f"Encryption failed: {e}")


def decrypt_password(encrypted_b64: str) -> Optional[str]:
    """
    Decrypt password using DPAPI (Windows only)

    Args:
        encrypted_b64: Base64-encoded encrypted password

    Returns:
        Decrypted password or None if decryption fails
    """
    if not DPAPI_AVAILABLE:
        logger.warning("DPAPI not available - cannot decrypt")
        return None

    if not encrypted_b64:
        return None

    try:
        # Decode from base64
        encrypted_bytes = base64.b64decode(encrypted_b64)

        # Decrypt using DPAPI
        decrypted_bytes = win32crypt.CryptUnprotectData(
            encrypted_bytes,
            None,   # Optional entropy
            None,   # Reserved
            None,   # Reserved
            0       # Flags
        )[1]  # Returns (description, data)

        return decrypted_bytes.decode('utf-8')

    except Exception as e:
        logger.error(f"Failed to decrypt password: {e}")
        return None


def is_available() -> bool:
    """Check if DPAPI encryption is available"""
    return DPAPI_AVAILABLE
