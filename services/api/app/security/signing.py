"""HMAC-SHA256 signing of commands sent to desktop agents (replay- and tamper-evident).

Signed string = the six fields below joined with a single LF. ``args_json`` is the exact
serialised JSON text that travels on the wire, so no canonicalisation is needed on the
agent: it verifies the received strings byte for byte, then parses ``args_json``.

The HMAC key is the UTF-8 bytes of the device secret. The desktop agent implements the
same construction (see docs/desktop-protocol.md).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

SIGNED_FIELDS: tuple[str, ...] = ("id", "action", "issued_at", "expires_at", "nonce", "args_json")


def serialize_args(args: Mapping[str, Any]) -> str:
    """Deterministic JSON text: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def signing_string(fields: Mapping[str, str]) -> str:
    return "\n".join(fields[name] for name in SIGNED_FIELDS)


def sign_fields(secret: str, fields: Mapping[str, str]) -> str:
    digest = hmac.new(secret.encode("utf-8"), signing_string(fields).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def verify_fields(secret: str, fields: Mapping[str, str], signature: str) -> bool:
    expected = sign_fields(secret, fields)
    return hmac.compare_digest(expected, signature)
