"""Encryption helpers for sensitive configuration values."""

import base64
import hashlib
import secrets


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


def _derive_key() -> bytes:
    """Derive a 32-byte Fernet key from machine-local seed (v2-compatible)."""
    import socket
    # Use v2 DB path for compatibility with migrated encrypted values
    v2_db = r"C:\Users\benz-\Documents\tradingai\alphaloop\alphaloop.db"
    seed = f"{v2_db}:{socket.gethostname()}:alphaloop-settings-v1"
    raw = hashlib.sha256(seed.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a value for storage. Returns 'enc::...' prefixed ciphertext."""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        ct = f.encrypt(plaintext.encode()).decode()
        return f"{_ENC_PREFIX}{ct}"
    except ImportError:
        return plaintext
    except Exception:
        return plaintext


def decrypt_value(stored: str) -> str:
    """Decrypt a value if it has the enc:: prefix. Returns plaintext."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        ct = stored[len(_ENC_PREFIX):]
        return f.decrypt(ct.encode()).decode()
    except ImportError:
        return stored[len(_ENC_PREFIX):]
    except Exception:
        return stored
