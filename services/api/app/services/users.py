"""Owner-managed member accounts: listing, creation, deactivation, password reset and session revocation.

Functions only flush. The calling route records the audit event and commits, so the change and its audit
row land in the same transaction.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.time import utc_now
from app.models.identity import User, UserSession
from app.security.passwords import hash_password, validate_new_password

ROLE_OWNER: Final = "owner"
ROLE_MEMBER: Final = "member"


async def list_users(db: AsyncSession) -> list[User]:
    rows = await db.scalars(select(User).order_by(User.created_at, User.email))
    return list(rows.all())


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.", code="user_not_found")
    return user


async def revoke_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revokes every live session of one user. Returns how many were revoked."""
    result = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    return int(result.rowcount or 0)


async def create_member(db: AsyncSession, *, email: str, display_name: str, password: str) -> User:
    """Creates a member. ``email`` must already be normalised; the schema does that."""
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("That email already has an account.", code="email_taken")
    validate_new_password(password)
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        role=ROLE_MEMBER,
        is_active=True,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        # A concurrent create won the race for the same email. The unique constraint is the final check.
        await db.rollback()
        raise ConflictError("That email already has an account.", code="email_taken") from exc
    return user


async def update_user(
    db: AsyncSession,
    *,
    target: User,
    is_active: bool | None,
    display_name: str | None,
) -> int:
    """Applies a PATCH. Deactivation revokes the user's sessions. Returns the number of sessions revoked."""
    if target.role == ROLE_OWNER and is_active is False:
        raise ValidationAppError("The owner account cannot be deactivated.", code="owner_protected")
    if display_name is not None:
        target.display_name = display_name
    revoked = 0
    if is_active is not None:
        target.is_active = is_active
        if not is_active:
            revoked = await revoke_sessions(db, target.id)
    return revoked


async def set_member_password(db: AsyncSession, *, target: User, new_password: str) -> int:
    """Sets a member's password and revokes that member's sessions. Returns the number of sessions revoked."""
    if target.role == ROLE_OWNER:
        raise ValidationAppError(
            "Change the owner password from the account settings.",
            code="owner_protected",
        )
    validate_new_password(new_password)
    target.password_hash = hash_password(new_password)
    return await revoke_sessions(db, target.id)
