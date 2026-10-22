"""Settings document: defaults, strict validation, ranges, enumerations, the fallback rule and merge helpers."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.settings import SECTION_NAMES, SettingsDoc
from app.services.settings_service import _changed_paths, _known_only, _touches_emergency_stop, defaults_document, deep_merge

DOCUMENTED_DEFAULTS: dict[str, Any] = {
    "timezone": None,
    "ai": {
        "active_provider": "gemini",
        "fallback_provider": None,
        "fallback_enabled": False,
        "daily_call_limit": None,
        "temperature": 0.7,
        "max_output_tokens": 2048,
        "system_instructions": "",
        "stream_responses": True,
        "include_memory": True,
    },
    "voice": {
        "language": "en-US",
        "voice_name": None,
        "speaking_rate": 1.0,
        "pitch": 1.0,
        "captions_enabled": True,
        "auto_speak": True,
        "interruptions_enabled": True,
        "push_to_talk": True,
        "continuous_listening": False,
        "wake_word_enabled": False,
        "microphone_device_id": None,
        "audio_output_device_id": None,
    },
    "appearance": {
        "theme": "dark",
        "accent_color": "#22d3ee",
        "orb_style": "orb",
        "animation_intensity": "normal",
        "reduced_motion": "system",
        "font_size": "medium",
        "sidebar_hidden": [],
        "dashboard_widgets": {
            "status": True,
            "devices": True,
            "tasks": True,
            "activity": True,
            "schedule": True,
            "quick_actions": True,
        },
    },
    "automation": {
        "max_concurrent_tasks": 3,
        "default_task_timeout_seconds": 120,
        "disabled_tools": [],
        "emergency_stopped": False,
    },
    "notifications": {
        "desktop_notifications": False,
        "on_task_completed": True,
        "on_task_failed": True,
        "on_approval_required": True,
        "on_device_disconnected": True,
        "on_integration_error": True,
        "on_schedule_event": True,
        "on_security_event": True,
        "include_task_details": False,
        "quiet_hours_enabled": False,
        "quiet_hours_start": None,
        "quiet_hours_end": None,
    },
    "memory": {"enabled": True, "retention_days": None, "auto_save_summaries": False},
    "privacy": {"store_conversations": True},
}


def _with(section: str, key: str, value: Any) -> dict[str, Any]:
    return {section: {key: value}}


def test_defaults_are_complete_and_match_the_documented_values() -> None:
    dumped = SettingsDoc().model_dump(mode="json")
    assert set(dumped) == set(DOCUMENTED_DEFAULTS) == set(SECTION_NAMES)
    for section, expected in DOCUMENTED_DEFAULTS.items():
        assert dumped[section] == expected, section


def test_defaults_document_round_trips() -> None:
    assert SettingsDoc.model_validate(defaults_document()).model_dump(mode="json") == defaults_document()


@pytest.mark.parametrize(
    "payload",
    [
        {"bogus": True},
        {"ai": {"model": "x"}},
        {"voice": {"volume": 3}},
        {"appearance": {"dashboard_widgets": {"weather": True}}},
        {"privacy": {"store_conversations": True, "share": True}},
    ],
)
def test_unknown_keys_are_rejected_at_every_level(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SettingsDoc.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("ai", "temperature", -0.1),
        ("ai", "temperature", 2.1),
        ("ai", "max_output_tokens", 63),
        ("ai", "max_output_tokens", 8193),
        ("ai", "system_instructions", "x" * 4001),
        ("voice", "speaking_rate", 0.49),
        ("voice", "speaking_rate", 2.01),
        ("voice", "pitch", 0.4),
        ("voice", "pitch", 2.5),
        ("automation", "max_concurrent_tasks", 0),
        ("automation", "max_concurrent_tasks", 11),
        ("automation", "default_task_timeout_seconds", 9),
        ("automation", "default_task_timeout_seconds", 1801),
        ("memory", "retention_days", 0),
        ("memory", "retention_days", 3651),
        ("appearance", "accent_color", "22d3ee"),
        ("appearance", "accent_color", "#22d3e"),
        ("appearance", "accent_color", "#22d3eg"),
        ("ai", "active_provider", "openai"),
        ("appearance", "theme", "sepia"),
        ("appearance", "orb_style", "cube"),
        ("appearance", "font_size", "huge"),
        ("appearance", "sidebar_hidden", ["Bad Id"]),
        ("automation", "disabled_tools", ["Not A Tool"]),
    ],
)
def test_out_of_range_and_unknown_enumeration_values_are_rejected(section: str, key: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        SettingsDoc.model_validate(_with(section, key, value))


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("ai", "temperature", 0),
        ("ai", "temperature", 2),
        ("ai", "max_output_tokens", 64),
        ("ai", "max_output_tokens", 8192),
        ("voice", "speaking_rate", 0.5),
        ("voice", "speaking_rate", 2.0),
        ("automation", "max_concurrent_tasks", 1),
        ("automation", "max_concurrent_tasks", 10),
        ("automation", "default_task_timeout_seconds", 10),
        ("automation", "default_task_timeout_seconds", 1800),
        ("memory", "retention_days", None),
        ("memory", "retention_days", 1),
        ("memory", "retention_days", 3650),
        ("appearance", "accent_color", "#ABCDEF"),
    ],
)
def test_boundary_values_are_accepted(section: str, key: str, value: Any) -> None:
    doc = SettingsDoc.model_validate(_with(section, key, value))
    assert doc.model_dump(mode="json")[section][key] == value


def test_booleans_are_not_accepted_as_numbers_or_strings() -> None:
    with pytest.raises(ValidationError):
        SettingsDoc.model_validate(_with("ai", "temperature", True))
    with pytest.raises(ValidationError):
        SettingsDoc.model_validate(_with("ai", "stream_responses", "false"))
    with pytest.raises(ValidationError):
        SettingsDoc.model_validate(_with("memory", "enabled", 0))


def test_fallback_equal_to_active_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        SettingsDoc.model_validate({"ai": {"active_provider": "gemini", "fallback_provider": "gemini"}})


def test_fallback_may_be_null_or_the_other_provider() -> None:
    assert SettingsDoc.model_validate({"ai": {"active_provider": "gemini", "fallback_provider": None}})
    other = SettingsDoc.model_validate({"ai": {"active_provider": "gemini", "fallback_provider": "huggingface"}})
    assert other.ai.fallback_provider == "huggingface"


def test_nested_dashboard_widgets_merge_per_key() -> None:
    doc = SettingsDoc.model_validate(deep_merge(defaults_document(), _with("appearance", "dashboard_widgets", {"tasks": False})))
    assert doc.appearance.dashboard_widgets.tasks is False
    assert doc.appearance.dashboard_widgets.status is True


def test_emergency_stop_is_detected_in_a_patch_whatever_its_value() -> None:
    assert _touches_emergency_stop({"automation": {"emergency_stopped": False}})
    assert _touches_emergency_stop({"automation": {"emergency_stopped": True, "max_concurrent_tasks": 2}})
    assert not _touches_emergency_stop({"automation": {"max_concurrent_tasks": 2}})
    assert not _touches_emergency_stop({"ai": {"temperature": 1}})


def test_deep_merge_changes_one_key_and_keeps_its_siblings() -> None:
    base = defaults_document()
    merged = deep_merge(base, {"ai": {"temperature": 1.2}})
    assert merged["ai"]["temperature"] == 1.2
    assert merged["ai"]["max_output_tokens"] == base["ai"]["max_output_tokens"]
    assert merged["voice"] == base["voice"]


def test_changed_paths_names_keys_without_values() -> None:
    before = defaults_document()
    after = deep_merge(before, {"ai": {"system_instructions": "secret wording"}})
    changed = _changed_paths(before, after)
    assert changed == ["ai.system_instructions"]
    assert "secret wording" not in str(changed)


def test_unknown_stored_keys_are_dropped_on_read() -> None:
    defaults = defaults_document()
    kept = _known_only({"ai": {"temperature": 0.3, "legacy": 1}, "old_section": {}}, defaults)
    assert kept == {"ai": {"temperature": 0.3}}


def test_timezone_is_optional_and_must_be_an_iana_name() -> None:
    from pydantic import ValidationError

    assert SettingsDoc().timezone is None
    assert SettingsDoc.model_validate({"timezone": "Asia/Kolkata"}).timezone == "Asia/Kolkata"
    with pytest.raises(ValidationError):
        SettingsDoc.model_validate({"timezone": "Mars/Olympus"})
