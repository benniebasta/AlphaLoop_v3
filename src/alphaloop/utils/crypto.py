"""Encryption helpers for sensitive configuration values."""

import base64
import hashlib
import logging
import os
import secrets
import uuid

logger = logging.getLogger(__name__)

_CRYPTO_AVAILABLE: bool | None = None


def _check_crypto() -> bool:
    """Check if cryptography package is available."""
    global _CRYPTO_AVAILABLE
    if _CRYPTO_AVAILABLE is None:
        try:
            from cryptography.fernet import Fernet  # noqa: F401
            _CRYPTO_AVAILABLE = True
        except ImportError:
            _CRYPTO_AVAILABLE = False
    return _CRYPTO_AVAILABLE


def generate_key() -> str:
    """Generate a random 32-byte key encoded as base64."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def simple_hash(value: str) -> str:
    """SHA256 hash of a string value."""
    return hashlib.sha256(value.encode()).hexdigest()


def mask_api_key(key: str, visible_chars: int = 4) -> str:
    """Mask an API key showing only the last N chars."""
    if not key or len(key) <= visible_chars:
        return "***"
    return f"***{key[-visible_chars:]}"


# ── Fernet encryption (compatible with v2 settings_store.py) ─────────────────

_ENC_PREFIX = "enc::"
_SENSITIVE_SUFFIXES = ("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET")


def is_sensitive(key: str) -> bool:
    """Check if a settings key should be encrypted."""
    return any(key.upper().endswith(s) for s in _SENSITIVE_SUFFIXES)


def _get_machine_id() -> str:
    """Get a stable machine identifier. Falls back to hostname if UUID unavailable."""
    try:
        return str(uuid.getnode())
    except Exception:
        import socket
        return socket.gethostname()


def _derive_key() -> bytes:
    """Derive a 32-byte Fernet key from machine-local seed (v2-compatible)."""
    import socket
    # Use v2 DB path for compatibility with migrated encrypted values
    v2_db = r"C:\Users\benz-\Documents\tradingai\alphaloop\alphaloop.db"
    seed = f"{v2_db}:{socket.gethostname()}:alphaloop-settings-v1"
    raw = hashlib.sha256(seed.encode()).digest()
    return base64.urlsafe_b64encode(raw)



def _is_production() -> bool:
    """Check if running in production environment."""
    return os.environ.get("ENVIRONMENT", "dev").lower() in ("production", "prod")


def encrypt_value(plaintext: str) -> str:
    """Encrypt a value for storage. Returns 'enc::...' prefixed ciphertext.

    In production mode, raises RuntimeError if cryptography is unavailable.
    """
    if not _check_crypto():
        if _is_production():
            logger.critical(
                "[crypto] FATAL: cryptography package not installed in PRODUCTION mode. "
                "Sensitive data CANNOT be stored securely. Install with: pip install cryptography"
            )
            raise RuntimeError(
                "cryptography package required in production mode for secret storage"
            )
        logger.warning(
            "[crypto] cryptography package not installed — storing value as PLAINTEXT. "
            "Install cryptography for encrypted storage."
        )
        return plaintext

    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        ct = f.encrypt(plaintext.encode()).decode()
        return f"{_ENC_PREFIX}{ct}"
    except Exception as e:
        logger.error("[crypto] Encryption failed: %s", e)
        if _is_production():
            raise RuntimeError(f"Encryption failed in production mode: {e}") from e
        return plaintext


def decrypt_value(stored: str) -> str:
    """Decrypt a value if it has the enc:: prefix. Returns plaintext."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored

    if not _check_crypto():
        if _is_production():
            logger.critical(
                "[crypto] FATAL: Cannot decrypt in PRODUCTION without cryptography package"
            )
            raise RuntimeError(
                "cryptography package required in production mode for secret decryption"
            )
        logger.warning("[crypto] Cannot decrypt without cryptography — returning raw ciphertext")
        return stored[len(_ENC_PREFIX):]

    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        ct = stored[len(_ENC_PREFIX):]
        return f.decrypt(ct.encode()).decode()
    except Exception as e:
        logger.error("[crypto] Decryption failed: %s", e)
        return stored
