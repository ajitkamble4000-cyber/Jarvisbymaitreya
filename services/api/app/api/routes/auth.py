"""Authentication: first-run owner setup, login/logout, sessions and password change."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, text, update

from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, CurrentSession, CurrentUser, DbSession, SettingsDep
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, RateLimitedError, UnauthorizedError
from app.core.time import utc_now
from app.models.identity import User, UserSession
from app.schemas.auth import AuthStatus, LoginRequest, PasswordChangeRequest, SessionOut, SetupRequest, UserOut, normalise_email
from app.security.passwords import hash_password, password_needs_rehash, validate_new_password, verify_password
from app.security.tokens import new_token, sha256_hex
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _set_auth_cookies(response: Response, settings: SettingsDep, session_token: str) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Double-submit CSRF token. Readable by the web app, which echoes it in X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE,
        new_token(24),
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _ensure_csrf_cookie(response: Response, request: Request, settings: SettingsDep) -> None:
    if request.cookies.get(CSRF_COOKIE):
        return
    response.set_cookie(
        CSRF_COOKIE,
        new_token(24),
        max_age=settings.session_ttl_hours * 3600,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def _create_session(db: DbSession, request: Request, user: User, settings: SettingsDep) -> str:
    token = new_token(32)
    now = utc_now()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=sha256_hex(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
            ip_address=_client_ip(request)[:64],
        )
    )
    return token


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request, response: Response, db: DbSession, settings: SettingsDep) -> AuthStatus:
    _ensure_csrf_cookie(response, request, settings)
    user_count = await db.scalar(select(func.count(User.id)))
    setup_required = not user_count
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return AuthStatus(setup_required=setup_required, authenticated=False)
    row = await db.scalar(select(UserSession).where(UserSession.token_hash == sha256_hex(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= utc_now():
        return AuthStatus(setup_required=setup_required, authenticated=False)
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        return AuthStatus(setup_required=setup_required, authenticated=False)
    return AuthStatus(setup_required=setup_required, authenticated=True, user=_user_out(user))


# Arbitrary fixed key for pg_advisory_xact_lock. It only has to be distinct from other advisory locks.
SETUP_LOCK_KEY = 0x4A41525649530001


@router.post("/setup", response_model=UserOut, status_code=201)
async def setup_owner(
    body: SetupRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
) -> UserOut:
    """Creates the single owner account. Allowed only while no user exists."""
    if not request.app.state.rate_limiter.hit(f"setup:{_client_ip(request)}", settings.login_rate_limit_per_minute):
        raise RateLimitedError("Too many attempts. Wait a minute and try again.")
    validate_new_password(body.password)
    # Transaction-scoped lock: two setup requests cannot both see zero users and both create an owner.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK_KEY})
    existing = await db.scalar(select(func.count(User.id)))
    if existing:
        raise ConflictError("Setup is already complete. Sign in instead.", code="setup_complete")

    user = User(
        email=body.email,
        display_name=body.display_name.strip() or body.email.split("@", 1)[0],
        password_hash=hash_password(body.password),
        role="owner",
        is_active=True,
        last_login_at=utc_now(),
    )
    db.add(user)
    await db.flush()
    token = await _create_session(db, request, user, settings)
    record_audit(db, action="auth.setup", user_id=user.id, ip_address=_client_ip(request))
    await db.commit()
    _set_auth_cookies(response, settings, token)
    return _user_out(user)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
) -> UserOut:
    ip = _client_ip(request)
    if not request.app.state.rate_limiter.hit(f"login:{ip}", settings.login_rate_limit_per_minute):
        raise RateLimitedError("Too many sign-in attempts. Wait a minute and try again.")

    try:
        email = normalise_email(body.email)
    except ValueError:
        email = ""
    user = await db.scalar(select(User).where(User.email == email)) if email else None
    # Always run one hash comparison so response timing does not reveal whether the email exists.
    password_ok = verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
    if user is None or not password_ok or not user.is_active:
        record_audit(
            db,
            action="auth.login",
            outcome="failure",
            actor_type="anonymous",
            data={"reason": "invalid_credentials"},
            ip_address=ip,
        )
        await db.commit()
        raise UnauthorizedError("Email or password is incorrect.", code="invalid_credentials")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utc_now()
    token = await _create_session(db, request, user, settings)
    record_audit(db, action="auth.login", user_id=user.id, ip_address=ip)
    await db.commit()
    _set_auth_cookies(response, settings, token)
    return _user_out(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DbSession, settings: SettingsDep) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.execute(
            update(UserSession)
            .where(UserSession.token_hash == sha256_hex(token), UserSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.cookie_secure, samesite="lax")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return _user_out(user)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(user: CurrentUser, session: CurrentSession, db: DbSession) -> list[SessionOut]:
    rows = (
        await db.scalars(
            select(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > utc_now())
            .order_by(UserSession.last_seen_at.desc())
        )
    ).all()
    return [
        SessionOut(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            user_agent=row.user_agent,
            ip_address=row.ip_address,
            current=row.id == session.id,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    row = await db.scalar(select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user.id))
    if row is None:
        raise NotFoundError("Session not found.")
    row.revoked_at = utc_now()
    record_audit(db, action="auth.session_revoked", user_id=user.id, target_type="session", target_id=str(row.id))
    await db.commit()
    return Response(status_code=204)


@router.post("/password", status_code=204)
async def change_password(body: PasswordChangeRequest, user: CurrentUser, session: CurrentSession, db: DbSession) -> Response:
    if not verify_password(user.password_hash, body.current_password):
        raise ForbiddenError("Current password is incorrect.", code="invalid_credentials")
    validate_new_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    # Revoke every other session so a stolen cookie stops working.
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.id != session.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    record_audit(db, action="auth.password_changed", user_id=user.id)
    await db.commit()
    return Response(status_code=204)


# A valid Argon2 hash of a random string, used to equalise timing for unknown emails.
_DUMMY_HASH = hash_password(new_token(24))
