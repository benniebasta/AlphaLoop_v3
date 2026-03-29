"""
Migrate config & API keys from AlphaLoop v1 database to v3.

v1 encrypts sensitive values (API keys, tokens, passwords) with Fernet
using a machine-local key derived from the DB path + hostname.
v3 stores settings as plaintext in the same app_settings table schema.

This script decrypts v1 values and writes them into the v3 database.

Usage:
    python scripts/migrate_v1_settings.py \
        --v1-db ../tradingai/alphaloop/alphaloop.db \
        --v3-db ./alphaloop.db
"""

import argparse
import base64
import hashlib
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_SENSITIVE_SUFFIXES = ("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET")
_ENC_PREFIX = "enc::"


def _is_sensitive(key: str) -> bool:
    return any(key.upper().endswith(s) for s in _SENSITIVE_SUFFIXES)


def _derive_v1_key(v1_db_path: str) -> bytes:
    """Derive the Fernet key using v1's exact derivation logic."""
    seed = f"{v1_db_path}:{socket.gethostname()}:alphaloop-settings-v1"
    raw = hashlib.sha256(seed.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _decrypt_value(stored: str, fernet_key: bytes) -> str:
    """Decrypt a v1 encrypted value. Returns plaintext."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    try:
        from cryptography.fernet import Fernet

        f = Fernet(fernet_key)
        ct = stored[len(_ENC_PREFIX) :]
        return f.decrypt(ct.encode()).decode()
    except ImportError:
        print("ERROR: 'cryptography' package required. Install with: pip install cryptography")
        sys.exit(1)
    except Exception as e:
        print(f"  WARNING: Failed to decrypt value: {e} — skipping")
        return ""


def _mask(value: str) -> str:
    """Mask a value for display: show first 4 and last 4 chars."""
    if len(value) <= 10:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def migrate(v1_db_path: str, v3_db_path: str) -> None:
    # Resolve absolute path for v1 DB (needed for key derivation)
    v1_abs = str(Path(v1_db_path).resolve())
    fernet_key = _derive_v1_key(v1_abs)

    v1 = sqlite3.connect(v1_db_path)
    v1.row_factory = sqlite3.Row

    # Ensure v3 DB and table exist
    v3 = sqlite3.connect(v3_db_path)
    v3.execute("PRAGMA journal_mode=WAL")
    v3.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT NOT NULL DEFAULT ''
        )
    """)

    try:
        rows = v1.execute("SELECT key, value FROM app_settings").fetchall()
    except Exception as e:
        print(f"ERROR: Could not read v1 app_settings: {e}")
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    migrated = 0
    skipped = 0

    for row in rows:
        key = row["key"]
        raw_value = row["value"]

        if not raw_value:
            skipped += 1
            continue

        # Decrypt if encrypted
        if raw_value.startswith(_ENC_PREFIX):
            value = _decrypt_value(raw_value, fernet_key)
            if not value:
                skipped += 1
                continue
            display = _mask(value)
            print(f"  [decrypted] {key} = {display}")
        else:
            value = raw_value
            if _is_sensitive(key):
                display = _mask(value)
            else:
                display = value if len(value) <= 60 else f"{value[:60]}..."
            print(f"  [plaintext] {key} = {display}")

        v3.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        migrated += 1

    v3.commit()
    v1.close()
    v3.close()

    print(f"\nMigration complete: {migrated} settings migrated, {skipped} skipped (empty/failed)")


def main():
    parser = argparse.ArgumentParser(description="Migrate v1 config & API keys to v3")
    parser.add_argument("--v1-db", required=True, help="Path to v1 SQLite database")
    parser.add_argument("--v3-db", required=True, help="Path to v3 SQLite database")
    args = parser.parse_args()

    if not Path(args.v1_db).exists():
        print(f"ERROR: v1 database not found at {args.v1_db}")
        sys.exit(1)

    print(f"Migrating settings from v1 ({args.v1_db}) to v3 ({args.v3_db})...\n")
    migrate(args.v1_db, args.v3_db)


if __name__ == "__main__":
    main()
