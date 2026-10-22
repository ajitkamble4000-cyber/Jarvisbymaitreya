"""Settings: read the complete document and apply partial updates (docs/api.md "Settings")."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError
from app.schemas.settings import SettingsDoc
from app.services.settings_service import load_settings, save_settings_patch

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsDoc)
async def read_settings(user: CurrentUser, db: DbSession) -> SettingsDoc:
    document = await load_settings(db, user.id)
    await db.commit()
    return document


@router.patch("", response_model=SettingsDoc)
async def update_settings(body: dict[str, Any], user: CurrentUser, db: DbSession) -> SettingsDoc:
    """Partial document, deep-merged per section. A refused patch still commits its audit record."""
    try:
        document = await save_settings_patch(db, user.id, body)
    except AppError:
        await db.commit()
        raise
    await db.commit()
    return document
