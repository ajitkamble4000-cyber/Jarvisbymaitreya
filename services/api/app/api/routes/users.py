"""User administration (owner only): list, create, update and reset member accounts.

Every route depends on ``require_owner``. A signed-in member gets 403 ``forbidden`` and a client with no
session gets 401 before any handler runs.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import DbSession, SettingsDep, require_owner
from app.core.errors import RateLimitedError
from app.models.identity import User
from app.schemas.users import AdminUserOut, PasswordResetRequest, UserCreate, UserListOut, UserUpdate
from app.services import users as users_service
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["users"])

OwnerDep = Annotated[User, Depends(require_owner)]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _admin_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.get("", response_model=UserListOut)
async def list_users(_owner: OwnerDep, db: DbSession) -> UserListOut:
    rows = await users_service.list_users(db)
    return UserListOut(items=[_admin_out(user) for user in rows])


@router.post("", response_model=AdminUserOut, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    owner: OwnerDep,
    db: DbSession,
    settings: SettingsDep,
) -> AdminUserOut:
    if not request.app.state.rate_limiter.hit(f"users-create:{owner.id}", settings.login_rate_limit_per_minute):
        raise RateLimitedError("Too many attempts. Wait a minute and try again.")
    user = await users_service.create_member(
        db,
        email=body.email,
        display_name=body.display_name,
        password=body.password,
    )
    record_audit(
        db,
        action="users.created",
        user_id=owner.id,
        target_type="user",
        target_id=str(user.id),
        data={"role": user.role},
        ip_address=_client_ip(request),
    )
    await db.commit()
    return _admin_out(user)


@router.patch("/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    owner: OwnerDep,
    db: DbSession,
) -> AdminUserOut:
    target = await users_service.get_user(db, user_id)
    revoked = await users_service.update_user(
        db,
        target=target,
        is_active=body.is_active,
        display_name=body.display_name,
    )
    data: dict[str, object] = {"sessions_revoked": revoked}
    if body.is_active is not None:
        data["is_active"] = body.is_active
    if body.display_name is not None:
        data["display_name_set"] = True
    record_audit(
        db,
        action="users.updated",
        user_id=owner.id,
        target_type="user",
        target_id=str(target.id),
        data=data,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return _admin_out(target)


@router.post("/{user_id}/password", status_code=204)
async def reset_user_password(
    user_id: uuid.UUID,
    body: PasswordResetRequest,
    request: Request,
    owner: OwnerDep,
    db: DbSession,
) -> Response:
    target = await users_service.get_user(db, user_id)
    revoked = await users_service.set_member_password(db, target=target, new_password=body.new_password)
    record_audit(
        db,
        action="users.password_reset",
        user_id=owner.id,
        target_type="user",
        target_id=str(target.id),
        data={"sessions_revoked": revoked},
        ip_address=_client_ip(request),
    )
    await db.commit()
    return Response(status_code=204)
