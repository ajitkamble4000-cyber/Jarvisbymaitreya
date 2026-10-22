"""Symmetric encryption for stored secrets (provider keys, device secrets, OAuth tokens).

Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The master key comes
from ENCRYPTION_KEY. In development and test an unset key is generated once into
DATA_DIR/secret.key with owner-only permissions. Production refuses to start without it.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import ConfigurationError, Settings


class SecretDecryptionError(RuntimeError):
    """The stored ciphertext could not be decrypted with the configured key."""


class SecretBox:
    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("ENCRYPTION_KEY must be a urlsafe base64 32-byte Fernet key.") from exc
        # Derive a separate sub-key for HMAC purposes so the Fernet key is never reused directly.
        self._master = hashlib.sha256(key if isinstance(key, bytes) else key.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise SecretDecryptionError("Stored secret could not be decrypted with the current key.") from exc

    def derive_key(self, purpose: str) -> bytes:
        """Purpose-bound 32-byte key, e.g. for keyed hashing of pairing codes."""
        return hashlib.sha256(self._master + b"|" + purpose.encode("utf-8")).digest()


def mask_secret(value: str) -> str:
    """Display hint such as ``…a1b2``. Never reveal more than the last four characters."""
    return f"…{value[-4:]}" if len(value) >= 8 else "…"


def build_secret_box(settings: Settings) -> SecretBox:
    if settings.encryption_key:
        return SecretBox(settings.encryption_key.get_secret_value())
    if settings.is_production:
        raise ConfigurationError("ENCRYPTION_KEY is required in production.")

    key_path: Path = settings.data_dir / "secret.key"
    if key_path.exists():
        key = key_path.read_text(encoding="ascii").strip()
    else:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key().decode("ascii")
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(key)
    return SecretBox(key)
