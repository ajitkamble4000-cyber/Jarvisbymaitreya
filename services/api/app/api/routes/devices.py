"""Device registry, pairing and the agent WebSocket (docs/api.md "Devices", docs/desktop-protocol.md).

The router has no prefix: every path is written in full, because the agent-facing paths sit under
``/agent`` and the device management paths under ``/devices``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Request, Response, WebSocket
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, SettingsDep
from app.core.errors import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)
from app.core.time import utc_now
from app.devices.hub import (
    DEFAULT_PING_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    DeviceHub,
)
from app.devices.pairing import (
    PAIRING_CODE_KEY_PURPOSE,
    generate_pairing_code,
    hash_pairing_code,
)
from app.devices.permissions import default_permissions, validate_permissions
from app.models.devices import Device, DevicePairingCode
from app.schemas.devices import (
    DeviceListOut,
    DeviceOut,
    DeviceUpdate,
    PairingCodeOut,
    PairRequest,
    PairResponse,
    PingOut,
    RevokeRequest,
    device_out,
    filter_capabilities,
    sanitise_resources,
)
from app.security.tokens import new_token, sha256_hex
from app.services.audit import record_audit

router = APIRouter(tags=["devices"])

_INVALID_PAIRING_MESSAGE = "Pairing code is invalid or has expired."


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _hub(request: Request) -> DeviceHub:
    hub: DeviceHub = request.app.state.device_hub
    return hub


def _connection_status(hub: DeviceHub, device: Device) -> str:
    if device.pairing_status == "revoked":
        return "revoked"
    return "online" if hub.is_online(device.id) else "offline"


def _parse_device_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError("Device not found.", code="device_not_found") from exc


async def _owned_device(db: AsyncSession, user_id: uuid.UUID, raw_id: str) -> Device:
    """Another user's device is reported exactly like a missing one, so ids cannot be probed."""
    device_id = _parse_device_id(raw_id)
    device = await db.scalar(select(Device).where(Device.id == device_id, Device.user_id == user_id))
    if device is None:
        raise NotFoundError("Device not found.", code="device_not_found")
    return device


def _publish_device(request: Request, user_id: uuid.UUID, device_id: uuid.UUID, connection_status: str) -> None:
    request.app.state.events.publish(
        user_id,
        "device.updated",
        {"device_id": str(device_id), "connection_status": connection_status},
    )


# ---- pairing ----------------------------------------------------------------------------

@router.post("/devices/pairing-codes", response_model=PairingCodeOut, status_code=201)
async def create_pairing_code(request: Request, user: CurrentUser, db: DbSession, settings: SettingsDep) -> PairingCodeOut:
    """Issue a single active pairing code. Earlier unused codes for the user are invalidated. The code is shown once."""
    secret_box = request.app.state.secret_box
    code = generate_pairing_code()
    now = utc_now()
    expires_at = now + timedelta(seconds=settings.device_pairing_code_ttl_seconds)
    await db.execute(
        update(DevicePairingCode)
        .where(
            DevicePairingCode.user_id == user.id,
            DevicePairingCode.used_at.is_(None),
            DevicePairingCode.invalidated_at.is_(None),
        )
        .values(invalidated_at=now)
    )
    db.add(
        DevicePairingCode(
            user_id=user.id,
            code_hash=hash_pairing_code(secret_box.derive_key(PAIRING_CODE_KEY_PURPOSE), code),
            created_at=now,
            expires_at=expires_at,
            failed_attempts=0,
        )
    )
    record_audit(db, action="device.pairing_code_created", user_id=user.id, target_type="device_pairing_code")
    await db.commit()
    return PairingCodeOut(code=code, expires_at=expires_at)


async def _record_failed_attempt(db: AsyncSession, ip: str) -> None:
    """Audit a wrong code. A wrong guess cannot be matched to a row, and it does not touch any stored code.

    Charging a guess to a live code would let anyone without a code cancel the owner's pairing code. Guessing
    is limited per client address by the rate limiter, and the codes are long enough that guessing cannot
    succeed within their lifetime.
    """
    record_audit(
        db,
        action="device.pair_failed",
        outcome="failure",
        actor_type="anonymous",
        ip_address=ip,
        data={"reason": "pairing_code_invalid"},
    )
    await db.commit()


@router.post("/agent/pair", response_model=PairResponse)
async def pair_agent(body: PairRequest, request: Request, db: DbSession, settings: SettingsDep) -> PairResponse:
    """Exchange a one-time code for a device secret. No cookie or CSRF token: the code is the credential."""
    ip = _client_ip(request)
    if not request.app.state.rate_limiter.hit(f"agent-pair:{ip}", settings.login_rate_limit_per_minute):
        raise RateLimitedError("Too many pairing attempts. Wait a minute and try again.")

    secret_box = request.app.state.secret_box
    code_hash = hash_pairing_code(secret_box.derive_key(PAIRING_CODE_KEY_PURPOSE), body.code)
    now = utc_now()
    row = await db.scalar(
        select(DevicePairingCode)
        .where(
            DevicePairingCode.code_hash == code_hash,
            DevicePairingCode.used_at.is_(None),
            DevicePairingCode.invalidated_at.is_(None),
            DevicePairingCode.expires_at > now,
        )
        .order_by(DevicePairingCode.created_at.desc())
        .limit(1)
    )
    if row is None:
        await _record_failed_attempt(db, ip)
        raise UnauthorizedError(_INVALID_PAIRING_MESSAGE, code="pairing_code_invalid")

    capabilities = filter_capabilities(body.capabilities)
    paired_before = await db.scalar(
        select(func.count())
        .select_from(Device)
        .where(Device.user_id == row.user_id, Device.pairing_status == "paired")
    )
    secret = new_token(32)
    device = Device(
        user_id=row.user_id,
        name=body.device_name,
        device_type=body.device_type,
        os=body.os,
        os_version=body.os_version,
        agent_version=body.agent_version,
        pairing_status="paired",
        is_default=not paired_before,
        capabilities=capabilities,
        permissions=default_permissions(capabilities),
        resources=sanitise_resources(body.resources),
        credential_hash=sha256_hex(secret),
        secret_ciphertext=secret_box.encrypt(secret),
        registered_at=now,
        paired_at=now,
    )
    db.add(device)
    await db.flush()

    # Consume the code atomically: a concurrent redemption matches no row, and the transaction is rolled back.
    consumed = await db.execute(
        update(DevicePairingCode)
        .where(
            DevicePairingCode.id == row.id,
            DevicePairingCode.used_at.is_(None),
            DevicePairingCode.invalidated_at.is_(None),
            DevicePairingCode.expires_at > now,
        )
        .values(used_at=now, device_id=device.id)
        .returning(DevicePairingCode.id)
    )
    if consumed.scalar_one_or_none() is None:
        raise UnauthorizedError(_INVALID_PAIRING_MESSAGE, code="pairing_code_invalid")

    record_audit(
        db,
        action="device.paired",
        user_id=row.user_id,
        actor_type="device",
        actor_id=str(device.id),
        target_type="device",
        target_id=str(device.id),
        ip_address=ip,
        data={"device_type": device.device_type, "os": device.os, "capabilities": capabilities},
    )
    await db.commit()
    _publish_device(request, row.user_id, device.id, "offline")
    return PairResponse(
        device_id=device.id,
        device_secret=secret,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
    )


# ---- device management --------------------------------------------------------------

@router.get("/devices", response_model=DeviceListOut)
async def list_devices(request: Request, user: CurrentUser, db: DbSession) -> DeviceListOut:
    hub = _hub(request)
    rows = (
        await db.scalars(select(Device).where(Device.user_id == user.id).order_by(Device.registered_at))
    ).all()
    return DeviceListOut(items=[device_out(row, _connection_status(hub, row)) for row in rows])


@router.patch("/devices/{device_id}", response_model=DeviceOut)
async def update_device(device_id: str, body: DeviceUpdate, request: Request, user: CurrentUser, db: DbSession) -> DeviceOut:
    device = await _owned_device(db, user.id, device_id)
    if device.pairing_status == "revoked":
        raise ConflictError("A revoked device cannot be changed.", code="device_revoked")

    # Validate everything before the first write, so a rejected request leaves no partial change.
    permissions_patch = validate_permissions(body.permissions) if body.permissions is not None else None

    changed: list[str] = []
    if body.name is not None:
        device.name = body.name
        changed.append("name")
    if body.device_type is not None:
        device.device_type = body.device_type
        changed.append("device_type")
    if permissions_patch is not None:
        device.permissions = {**(device.permissions or {}), **permissions_patch}
        changed.append("permissions")
    if body.is_default is not None:
        if body.is_default:
            # Clear the flag on the user's other devices first; the partial unique index allows one default only.
            await db.execute(
                update(Device)
                .where(Device.user_id == user.id, Device.id != device.id, Device.is_default.is_(True))
                .values(is_default=False)
            )
            device.is_default = True
        else:
            device.is_default = False
        changed.append("is_default")

    if changed:
        record_audit(
            db,
            action="device.updated",
            user_id=user.id,
            target_type="device",
            target_id=str(device.id),
            data={"fields": changed},
        )
    await db.commit()
    hub = _hub(request)
    return device_out(device, _connection_status(hub, device))


@router.post("/devices/{device_id}/ping", response_model=PingOut)
async def ping_device(device_id: str, request: Request, user: CurrentUser, db: DbSession) -> PingOut:
    device = await _owned_device(db, user.id, device_id)
    if device.pairing_status == "revoked":
        return PingOut(online=False, latency_ms=None, message="Device is revoked.")
    hub = _hub(request)
    if not hub.is_online(device.id):
        return PingOut(online=False, latency_ms=None, message="Device is offline.")
    latency = await hub.ping(device.id, timeout_seconds=DEFAULT_PING_TIMEOUT_SECONDS)
    if latency is None:
        return PingOut(online=False, latency_ms=None, message="Device did not answer in time.")
    return PingOut(online=True, latency_ms=latency, message="Device responded.")


@router.post("/devices/{device_id}/revoke", response_model=DeviceOut)
async def revoke_device(
    device_id: str,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    body: RevokeRequest | None = None,
) -> DeviceOut:
    """Revoke a device: the secret is erased, the socket is closed with 4403. Idempotent."""
    device = await _owned_device(db, user.id, device_id)
    hub = _hub(request)
    if device.pairing_status == "revoked":
        await hub.disconnect(device.id, "revoked")
        return device_out(device, "revoked")

    reason = (body.reason.strip() if body and body.reason else "") or "revoked by owner"
    now = utc_now()
    device.pairing_status = "revoked"
    device.revoked_at = now
    device.revoked_reason = reason[:200]
    device.credential_hash = None
    device.secret_ciphertext = None
    device.is_default = False
    record_audit(
        db,
        action="device.revoked",
        user_id=user.id,
        target_type="device",
        target_id=str(device.id),
        data={"reason": reason[:200]},
    )
    await db.commit()

    was_online = hub.is_online(device.id)
    await hub.disconnect(device.id, "revoked")
    if not was_online:
        # When a live socket was closed, the hub has already published the change.
        _publish_device(request, user.id, device.id, "revoked")
    return device_out(device, "revoked")


@router.delete("/devices/{device_id}", status_code=204)
async def delete_device(device_id: str, request: Request, user: CurrentUser, db: DbSession) -> Response:
    device = await _owned_device(db, user.id, device_id)
    if device.pairing_status != "revoked":
        raise ConflictError("Only revoked devices can be deleted. Revoke the device first.", code="device_not_revoked")
    record_audit(db, action="device.deleted", user_id=user.id, target_type="device", target_id=str(device.id))
    await db.delete(device)
    await db.commit()
    return Response(status_code=204)


# ---- agent WebSocket --------------------------------------------------------------------

@router.websocket("/agent/ws")
async def agent_websocket(websocket: WebSocket) -> None:
    hub: DeviceHub = websocket.app.state.device_hub
    await hub.handle_connection(websocket)
