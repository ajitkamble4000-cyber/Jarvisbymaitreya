"""Unit tests for security primitives that have no database dependency."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import ConfigurationError, Settings
from app.core.errors import ValidationAppError
from app.core.logging import redact
from app.devices.protocol import DEVICE_ACTIONS, build_cancel, build_command
from app.security.crypto import SecretBox, SecretDecryptionError, build_secret_box, mask_secret
from app.security.passwords import hash_password, validate_new_password, verify_password
from app.security.ratelimit import RateLimiter
from app.security.signing import SIGNED_FIELDS, serialize_args, sign_fields, verify_fields
from app.security.tokens import constant_time_equals, keyed_hash, sha256_hex
from app.services.audit import sanitize_audit_data
from app.services.events import EventBus


def test_password_hash_and_verify_round_trip() -> None:
    digest = hash_password("a-sufficiently-long-password")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "a-sufficiently-long-password")
    assert not verify_password(digest, "a-different-password-here")
    assert not verify_password("not-a-hash", "anything")


def test_password_policy_enforces_length() -> None:
    with pytest.raises(ValidationAppError):
        validate_new_password("short")
    validate_new_password("long-enough-password")


def test_keyed_hash_depends_on_key_and_sha256_is_stable() -> None:
    assert keyed_hash(b"k1", "ABCD") != keyed_hash(b"k2", "ABCD")
    assert sha256_hex("token") == sha256_hex("token")
    assert constant_time_equals("abc", "abc") and not constant_time_equals("abc", "abd")


def test_secret_box_round_trip_and_wrong_key_detection() -> None:
    key = Fernet.generate_key().decode()
    box = SecretBox(key)
    ciphertext = box.encrypt("AIzaSecretValue1234567890")
    assert "AIza" not in ciphertext
    assert box.decrypt(ciphertext) == "AIzaSecretValue1234567890"
    with pytest.raises(SecretDecryptionError):
        SecretBox(Fernet.generate_key().decode()).decrypt(ciphertext)
    with pytest.raises(ConfigurationError):
        SecretBox("not-a-valid-key")


def test_mask_secret_reveals_only_last_four() -> None:
    assert mask_secret("sk-abcdefgh1234") == "…1234"
    assert "abc" not in mask_secret("abcdef")


def test_production_requires_encryption_key() -> None:
    with pytest.raises(ConfigurationError):
        Settings(jarvis_env="production", public_base_url="https://jarvis.example", cookie_secure=True)


def test_build_secret_box_generates_key_in_development(tmp_path) -> None:
    settings = Settings(jarvis_env="development", data_dir=tmp_path, encryption_key=None)
    box = build_secret_box(settings)
    again = build_secret_box(settings)
    assert box.decrypt(box.encrypt("x")) == "x"
    assert again.decrypt(box.encrypt("y")) == "y"
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600


def test_signature_verifies_and_detects_tampering() -> None:
    secret = "device-secret-value"
    fields = {
        "id": str(uuid.uuid4()),
        "action": "launch_app",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-01-01T00:01:00Z",
        "nonce": "n" * 16,
        "args_json": serialize_args({"app": "chrome"}),
    }
    signature = sign_fields(secret, fields)
    assert verify_fields(secret, fields, signature)
    assert not verify_fields("other-secret", fields, signature)
    assert not verify_fields(secret, {**fields, "args_json": serialize_args({"app": "cmd"})}, signature)
    assert len(SIGNED_FIELDS) == 6


def test_serialize_args_is_deterministic() -> None:
    assert serialize_args({"b": 1, "a": "x"}) == '{"a":"x","b":1}'


def test_built_command_is_signed_over_its_wire_fields() -> None:
    secret = "device-secret-value"
    command = build_command(secret, action="open_url", args={"url": "https://youtube.com"}, ttl_seconds=60)
    fields = {
        "id": command.id,
        "action": command.action,
        "issued_at": command.issued_at,
        "expires_at": command.expires_at,
        "nonce": command.nonce,
        "args_json": command.args_json,
    }
    assert verify_fields(secret, fields, command.signature)
    assert command.action in DEVICE_ACTIONS


def test_build_command_rejects_unknown_actions() -> None:
    with pytest.raises(ValueError):
        build_command("s", action="run_shell", args={}, ttl_seconds=10)


def test_build_cancel_has_expiry_window() -> None:
    cancel = build_cancel("device-secret-value", command_id=str(uuid.uuid4()))
    issued = datetime.fromisoformat(cancel.issued_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(cancel.expires_at.replace("Z", "+00:00"))
    assert expires - issued == timedelta(seconds=60)
    assert issued.tzinfo is UTC or issued.utcoffset() == timedelta(0)


def test_redaction_removes_known_secret_shapes() -> None:
    text = "key=AIzaSyA1234567890abcdefghijklmn token: hf_abcdefghijklmnopqrstu Authorization: Bearer abcdefghijkl123456 password=hunter2"
    cleaned = redact(text)
    assert "AIza" not in cleaned and "hf_abc" not in cleaned and "hunter2" not in cleaned and "abcdefghijkl" not in cleaned


def test_audit_sanitizer_drops_sensitive_keys() -> None:
    clean = sanitize_audit_data({"app": "chrome", "api_key": "x", "nested": {"password": "y", "url": "https://a"}})
    assert clean == {"app": "chrome", "nested": {"url": "https://a"}}


def test_rate_limiter_blocks_after_limit_and_recovers() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert all(limiter.hit("ip", 3, 60) for _ in range(3))
    assert not limiter.hit("ip", 3, 60)
    now[0] = 61.0
    assert limiter.hit("ip", 3, 60)


async def test_event_bus_isolates_users() -> None:
    bus = EventBus()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    async with bus.subscribe(alice) as alice_queue, bus.subscribe(bob) as bob_queue:
        bus.publish(alice, "task.updated", {"id": "t1"})
        event = await alice_queue.get()
        assert event.type == "task.updated" and event.data == {"id": "t1"}
        assert bob_queue.empty()
