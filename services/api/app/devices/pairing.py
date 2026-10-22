"""Pairing codes: generation, normalisation and keyed hashing.

A pairing code is low entropy by design (the owner types it in). It is therefore never stored
in clear and never hashed without a key: the stored value is an HMAC keyed with a purpose-bound
key derived from ENCRYPTION_KEY, so a database dump alone is not enough to test guesses.
"""

from __future__ import annotations

import re
import secrets

from app.security.tokens import keyed_hash

PAIRING_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 8
PAIRING_CODE_KEY_PURPOSE = "pairing-code"

_SEPARATOR_OR_SPACE = re.compile(r"[\s-]")


def generate_pairing_code() -> str:
    """Return a code shaped ``XXXX-XXXX`` drawn from the unambiguous alphabet."""
    chars = "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
    return f"{chars[:4]}-{chars[4:]}"


def normalise_pairing_code(value: str) -> str:
    """Strip, upper-case and remove dashes and whitespace so typed variants compare equal."""
    return _SEPARATOR_OR_SPACE.sub("", value.strip().upper())


def hash_pairing_code(key: bytes, code: str) -> str:
    """Keyed hash (HMAC-SHA256 hex) of the normalised code."""
    return keyed_hash(key, normalise_pairing_code(code))
