"""Persisted per-user settings: the complete document, validated partial patches and the emergency-stop flag.

Storage is one ``UserPreference`` row per user. The row holds the complete validated document. Reads
fill any missing key from the defaults and drop keys the schema does not know. A stored document that
fails validation is never replaced by defaults: that could switch automation back on after an emergency
stop. The read fails closed with ``settings_unavailable`` instead.

Other modules import these functions. The caller owns the transaction and commits.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ValidationAppError
from app.core.time import utc_now
from app.models.identity import UserPreference
from app.schemas.settings import SettingsDoc
from app.services.audit import record_audit

log = logging.getLogger("jarvis.settings")

EMERGENCY_STOP_PATH = ("automation", "emergency_stopped")
_MAX_AUDIT_FIELDS = 50


def defaults_document() -> dict[str, Any]:
    return SettingsDoc().model_dump(mode="json")


def deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into ``base`` key by key. Nested mappings merge; every other value is replaced."""
    merged: dict[str, Any] = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _known_only(stored: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only keys the schema defines, recursively, so an unexpected stored key cannot break reads."""
    kept: dict[str, Any] = {}
    for key, value in stored.items():
        if key not in defaults:
            continue
        default_value = defaults[key]
        if isinstance(default_value, Mapping) and isinstance(value, Mapping):
            kept[key] = _known_only(value, default_value)
        else:
            kept[key] = value
    return kept


def _document_from_stored(stored: Any) -> SettingsDoc:
    defaults = defaults_document()
    stored_map: Mapping[str, Any] = stored if isinstance(stored, Mapping) else {}
    merged = deep_merge(defaults, _known_only(stored_map, defaults))
    try:
        return SettingsDoc.model_validate(merged)
    except ValidationError:
        # Field names only. Stored values are never logged.
        log.error("Stored settings document failed validation; refusing to fall back to defaults.")
        raise AppError(
            "Your settings could not be read. Save them again from the Settings page.",
            code="settings_unavailable",
            status_code=503,
        ) from None


async def _find_row(db: AsyncSession, user_id: uuid.UUID) -> UserPreference | None:
    return await db.scalar(
        select(UserPreference).where(UserPreference.user_id == user_id).execution_options(populate_existing=True)
    )


async def _ensure_row(db: AsyncSession, user_id: uuid.UUID) -> UserPreference:
    """Create the row with an empty document when it does not exist. Concurrent creators are harmless."""
    row = await _find_row(db, user_id)
    if row is None:
        now = utc_now()
        await db.execute(
            pg_insert(UserPreference)
            .values(user_id=user_id, settings={}, created_at=now, updated_at=now)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        row = await _find_row(db, user_id)
    if row is None:
        raise AppError("Your settings could not be read.", code="settings_unavailable", status_code=503)
    return row


async def _locked_row(db: AsyncSession, user_id: uuid.UUID) -> UserPreference:
    """Lock the settings row for the rest of the transaction so a patch and an emergency stop cannot interleave."""
    await _ensure_row(db, user_id)
    row = await db.scalar(
        select(UserPreference)
        .where(UserPreference.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise AppError("Your settings could not be read.", code="settings_unavailable", status_code=503)
    return row


async def load_settings(db: AsyncSession, user_id: uuid.UUID) -> SettingsDoc:
    """The complete settings document for ``user_id``. Creates the stored row on first read."""
    row = await _ensure_row(db, user_id)
    return _document_from_stored(row.settings)


def _patch_paths(patch: Mapping[str, Any], prefix: str = "") -> list[str]:
    """Dotted names of the keys a patch touches. Names only: values are never recorded."""
    paths: list[str] = []
    for key, value in patch.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping) and value:
            paths.extend(_patch_paths(value, f"{path}."))
        else:
            paths.append(path)
    return sorted(paths)[:_MAX_AUDIT_FIELDS]


def _changed_paths(before: Mapping[str, Any], after: Mapping[str, Any], prefix: str = "") -> list[str]:
    changed: list[str] = []
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}{key}"
        left, right = before.get(key), after.get(key)
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            changed.extend(_changed_paths(left, right, f"{path}."))
        elif left != right:
            changed.append(path)
    return changed


def _error_fields(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in err.get("loc", ())), "message": str(err.get("msg", ""))}
        for err in exc.errors(include_url=False, include_input=False, include_context=False)
    ]


def _touches_emergency_stop(patch: Mapping[str, Any]) -> bool:
    automation = patch.get("automation")
    return isinstance(automation, Mapping) and "emergency_stopped" in automation


async def save_settings_patch(db: AsyncSession, user_id: uuid.UUID, patch: Mapping[str, Any]) -> SettingsDoc:
    """Merge a partial document into the stored one, validate the result and persist it.

    Refused patches (read-only key, invalid merged result) raise ``AppError`` and change nothing. The refusal
    is still audited with field names, so the caller should commit the session before re-raising.
    """
    if _touches_emergency_stop(patch):
        record_audit(
            db,
            action="settings.updated",
            outcome="denied",
            user_id=user_id,
            actor_type="user",
            target_type="settings",
            data={"fields": _patch_paths(patch)},
        )
        raise AppError(
            "The emergency stop is changed with the emergency stop and resume controls, not with settings.",
            code="read_only_setting",
            status_code=422,
            details={"fields": [".".join(EMERGENCY_STOP_PATH)]},
        )

    row = await _locked_row(db, user_id)
    before = _document_from_stored(row.settings).model_dump(mode="json")
    try:
        document = SettingsDoc.model_validate(deep_merge(before, patch))
    except ValidationError as exc:
        record_audit(
            db,
            action="settings.updated",
            outcome="failure",
            user_id=user_id,
            actor_type="user",
            target_type="settings",
            data={"fields": _patch_paths(patch)},
        )
        raise ValidationAppError(
            "Some settings are invalid.", code="validation_error", details={"fields": _error_fields(exc)}
        ) from None

    after = document.model_dump(mode="json")
    row.settings = after
    record_audit(
        db,
        action="settings.updated",
        user_id=user_id,
        actor_type="user",
        target_type="settings",
        data={"changed": _changed_paths(before, after)[:_MAX_AUDIT_FIELDS]},
    )
    return document


async def set_emergency_stopped(db: AsyncSession, user_id: uuid.UUID, value: bool) -> None:
    """Set or clear ``automation.emergency_stopped``. Writes only that flag, so it cannot be blocked by other sections."""
    row = await _locked_row(db, user_id)
    stored = dict(row.settings) if isinstance(row.settings, Mapping) else {}
    automation = stored.get("automation")
    section = dict(automation) if isinstance(automation, Mapping) else {}
    section["emergency_stopped"] = bool(value)
    stored["automation"] = section
    row.settings = stored


__all__ = [
    "EMERGENCY_STOP_PATH",
    "defaults_document",
    "deep_merge",
    "load_settings",
    "save_settings_patch",
    "set_emergency_stopped",
]
