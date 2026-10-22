"""The account holder's own data as one JSON file (see app/services/account_export.py)."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import CurrentUser, DbSession
from app.services.account_export import build_account_export
from app.services.audit import record_audit

router = APIRouter(prefix="/account", tags=["account"])

FILENAME = "jarvis-account-export.json"


@router.get("/export")
async def export_account(db: DbSession, user: CurrentUser) -> Response:
    payload = await build_account_export(db, user)
    record_audit(db, action="account.exported", user_id=user.id, target_type="user", target_id=str(user.id), data={"sections": len(payload["sections"])})
    await db.commit()
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{FILENAME}"', "Cache-Control": "no-store"},
    )
