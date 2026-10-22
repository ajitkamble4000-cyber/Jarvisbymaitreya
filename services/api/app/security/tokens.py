"""Random tokens, hashing for storage, and constant-time comparison."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def new_token(nbytes: int = 32) -> str:
    """URL-safe random token with ``nbytes`` of entropy."""
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    """Hash for high-entropy tokens that are looked up by hash (sessions, device secrets)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def keyed_hash(key: bytes, value: str) -> str:
    """HMAC-SHA256 for low-entropy values such as pairing codes, so they cannot be brute-forced offline."""
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
