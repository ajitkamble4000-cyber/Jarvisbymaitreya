"""Pure unit tests: pairing codes, permission rules, resource sanitising, agent wire parsing, rate limiting."""

from __future__ import annotations

import hashlib
import re

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.errors import ValidationAppError
from app.devices.hub import FrameRateLimiter
from app.devices.pairing import (
    PAIRING_CODE_ALPHABET,
    generate_pairing_code,
    hash_pairing_code,
    normalise_pairing_code,
)
from app.devices.permissions import (
    LOW_RISK_CAPABILITIES,
    default_permissions,
    merge_default_permissions,
    validate_permissions,
)
from app.devices.protocol import (
    DEVICE_ACTIONS,
    AgentHeartbeat,
    AgentHello,
    AgentPong,
    AgentProgress,
    AgentResult,
    agent_message_adapter,
)
from app.schemas.devices import DeviceOut, filter_capabilities, sanitise_resources
from app.security.crypto import SecretBox

_CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$")


# ---- pairing codes ----------------------------------------------------------------------

def test_generated_codes_have_the_documented_shape() -> None:
    codes = {generate_pairing_code() for _ in range(200)}
    assert all(_CODE_RE.match(code) for code in codes)
    assert len(codes) > 190  # drawn at random, not a constant


def test_alphabet_leaves_out_ambiguous_characters() -> None:
    assert PAIRING_CODE_ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    for ambiguous in "01ILO":
        assert ambiguous not in PAIRING_CODE_ALPHABET


@pytest.mark.parametrize("typed", ["KQ7M-2XBV", "kq7m-2xbv", "  KQ7M 2XBV  ", "kq7m2xbv", "KQ7M-2XBV\n"])
def test_normalisation_ignores_case_dashes_and_spaces(typed: str) -> None:
    assert normalise_pairing_code(typed) == "KQ7M2XBV"


def test_keyed_hash_matches_across_spellings_and_differs_by_key() -> None:
    key_a = b"a" * 32
    key_b = b"b" * 32
    digest = hash_pairing_code(key_a, "KQ7M-2XBV")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == hash_pairing_code(key_a, "kq7m 2xbv")
    assert digest != hash_pairing_code(key_b, "KQ7M-2XBV")
    # An unkeyed hash of the same code is not what is stored.
    assert digest != hashlib.sha256(b"KQ7M2XBV").hexdigest()


def test_pairing_key_is_bound_to_its_purpose() -> None:
    box = SecretBox(Fernet.generate_key())
    assert box.derive_key("pairing-code") == box.derive_key("pairing-code")
    assert box.derive_key("pairing-code") != box.derive_key("something-else")


# ---- permissions ------------------------------------------------------------------------

def test_default_permissions_are_ask_for_every_reported_capability() -> None:
    assert default_permissions(["open_url", "launch_app"]) == {
        "open_url": {"mode": "ask"},
        "launch_app": {"mode": "ask"},
    }


def test_low_risk_set_is_a_subset_of_device_actions() -> None:
    assert {"open_url", "open_folder", "system_info", "app_availability"} == LOW_RISK_CAPABILITIES
    assert set(DEVICE_ACTIONS) >= LOW_RISK_CAPABILITIES


@pytest.mark.parametrize("capability", sorted(LOW_RISK_CAPABILITIES))
def test_allow_is_accepted_for_low_risk_capabilities(capability: str) -> None:
    assert validate_permissions({capability: {"mode": "allow"}}) == {capability: {"mode": "allow"}}


@pytest.mark.parametrize("capability", ["launch_app", "open_project", "read_file"])
def test_allow_is_rejected_for_other_capabilities(capability: str) -> None:
    with pytest.raises(ValidationAppError) as raised:
        validate_permissions({capability: {"mode": "allow"}})
    assert raised.value.code == "permission_not_allowed"
    assert raised.value.status_code == 422


@pytest.mark.parametrize("capability", DEVICE_ACTIONS)
@pytest.mark.parametrize("mode", ["ask", "deny"])
def test_ask_and_deny_are_accepted_for_every_capability(capability: str, mode: str) -> None:
    assert validate_permissions({capability: {"mode": mode}}) == {capability: {"mode": mode}}


def test_unknown_capability_is_rejected() -> None:
    with pytest.raises(ValidationAppError) as raised:
        validate_permissions({"format_disk": {"mode": "ask"}})
    assert raised.value.code == "validation_error"
    assert raised.value.status_code == 422


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "always"},
        {"mode": "ask", "scope": "everything"},
        {},
        "allow",
    ],
)
def test_malformed_permission_entries_are_rejected(policy: object) -> None:
    with pytest.raises(ValidationAppError) as raised:
        validate_permissions({"open_url": policy})
    assert raised.value.code == "validation_error"


def test_merge_defaults_keeps_existing_entries_and_adds_missing_ones() -> None:
    merged = merge_default_permissions({"open_url": {"mode": "deny"}}, ["open_url", "system_info", "format_disk"])
    assert merged == {"open_url": {"mode": "deny"}, "system_info": {"mode": "ask"}}


# ---- capabilities and resources ---------------------------------------------------------

def test_capabilities_are_filtered_to_device_actions_and_deduplicated() -> None:
    assert filter_capabilities(["open_url", "format_disk", "open_url", "launch_app"]) == ["open_url", "launch_app"]


def test_sanitiser_drops_unknown_keys_and_extra_entry_fields() -> None:
    raw = {
        "apps": [{"id": "chrome", "label": "Google Chrome", "path": "C:\\Program Files\\chrome.exe"}],
        "shell": [{"id": "x", "label": "x"}],
        "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com", "path": "C:\\x"}],
        "folders": "not-a-list",
    }
    out = sanitise_resources(raw)
    assert set(out) == {"apps", "urls", "folders", "projects", "files", "checks"}
    assert out["apps"] == [{"id": "chrome", "label": "Google Chrome"}]
    assert out["urls"] == [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}]
    assert out["folders"] == []
    assert "C:" not in repr(out)


def test_sanitiser_rejects_bad_ids_labels_and_non_http_urls() -> None:
    raw = {
        "apps": [
            {"id": "Bad Id", "label": "x"},
            {"id": "../etc", "label": "x"},
            {"id": "-leading", "label": "x"},
            {"id": "a" * 65, "label": "x"},
            {"id": "empty-label", "label": "   "},
            {"id": "long-label", "label": "x" * 121},
            {"id": "control", "label": "a\x00b"},
            {"label": "no id"},
            "string-entry",
            {"id": "ok-1", "label": "OK"},
        ],
        "urls": [
            {"id": "file", "label": "F", "url": "file:///C:/Windows"},
            {"id": "js", "label": "J", "url": "javascript:alert(1)"},
            {"id": "ftp", "label": "F", "url": "ftp://example.com/x"},
            {"id": "no-host", "label": "N", "url": "https://"},
            {"id": "no-url", "label": "N"},
            {"id": "too-long", "label": "T", "url": "https://example.com/" + "a" * 2100},
            {"id": "ok", "label": "OK", "url": "http://example.com"},
        ],
    }
    out = sanitise_resources(raw)
    assert [entry["id"] for entry in out["apps"]] == ["ok-1"]
    assert [entry["id"] for entry in out["urls"]] == ["ok"]


def test_sanitiser_caps_each_kind_at_fifty_unique_entries() -> None:
    apps = [{"id": f"app-{index}", "label": f"App {index}"} for index in range(60)]
    apps.append({"id": "app-1", "label": "Duplicate"})
    out = sanitise_resources({"apps": apps})
    assert len(out["apps"]) == 50
    assert len({entry["id"] for entry in out["apps"]}) == 50


def test_sanitiser_accepts_non_dict_input() -> None:
    assert sanitise_resources(None) == {
        "apps": [],
        "urls": [],
        "folders": [],
        "projects": [],
        "files": [],
        "checks": [],
    }


def test_public_device_shape_has_no_credential_fields() -> None:
    fields = set(DeviceOut.model_fields)
    assert {"credential_hash", "secret_ciphertext", "device_secret"}.isdisjoint(fields)


# ---- agent wire protocol ----------------------------------------------------------------

def test_unknown_agent_message_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        agent_message_adapter.validate_json('{"type": "run_shell", "cmd": "del *"}')


def test_non_json_frame_is_rejected() -> None:
    with pytest.raises(ValidationError):
        agent_message_adapter.validate_json("{not json")


def test_known_agent_messages_parse_to_their_models() -> None:
    hello = agent_message_adapter.validate_json(
        '{"type": "hello", "agent_version": "1.0.0", "os": "windows", "capabilities": ["open_url"], "resources": {}}'
    )
    heartbeat = agent_message_adapter.validate_json('{"type": "heartbeat", "running_command_ids": ["c1"], "sent_at": "x"}')
    progress = agent_message_adapter.validate_json(
        '{"type": "progress", "command_id": "c1", "step": "Starting", "sent_at": "x"}'
    )
    result = agent_message_adapter.validate_json(
        '{"type": "result", "command_id": "c1", "status": "succeeded", "output": {}, "finished_at": "x"}'
    )
    pong = agent_message_adapter.validate_json('{"type": "pong", "nonce": "n1"}')
    assert isinstance(hello, AgentHello)
    assert isinstance(heartbeat, AgentHeartbeat)
    assert isinstance(progress, AgentProgress)
    assert isinstance(result, AgentResult)
    assert isinstance(pong, AgentPong)


def test_result_status_is_restricted_to_terminal_values() -> None:
    with pytest.raises(ValidationError):
        agent_message_adapter.validate_json(
            '{"type": "result", "command_id": "c1", "status": "running", "output": {}, "finished_at": "x"}'
        )


# ---- frame rate limit -------------------------------------------------------------------

def test_frame_rate_limiter_allows_fifty_frames_per_second() -> None:
    now = [0.0]
    limiter = FrameRateLimiter(50, clock=lambda: now[0])
    assert all(limiter.allow() for _ in range(50))
    assert limiter.allow() is False
    now[0] = 0.5
    assert limiter.allow() is False
    now[0] = 1.01
    assert limiter.allow() is True
