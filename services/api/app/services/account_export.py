"""Everything the account holds, for the account holder: one JSON file they can keep or move to another service.

Each section lists its fields explicitly. A field that holds a secret is never selected: password hashes, session
tokens, device secrets, provider keys and their hints, integration credentials and error messages. A column added
later is therefore not exported unless it is listed here. The audit section leaves out IP addresses and event data.
Every query is scoped to the user, so a member never receives the owner's rows.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.ai import ProviderConfig
from app.models.conversation import Conversation, Message
from app.models.devices import Device
from app.models.identity import User
from app.models.platform import AuditEvent, Integration, Notification
from app.models.scheduling import ScheduledJob
from app.models.tasks import Task
from app.services import memory_service
from app.services.settings_service import load_settings

FORMAT_VERSION = 1


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _resource_labels(resources: dict[str, Any]) -> dict[str, list[str]]:
    """Labels only, per resource kind. Paths and URLs stay on the device and in the stored record, not in the export."""
    return {
        kind: [str(item.get("label", "")) for item in items if isinstance(item, dict)]
        for kind, items in resources.items()
        if isinstance(items, list)
    }


async def _profile(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "created_at": _iso(user.created_at),
        "last_login_at": _iso(user.last_login_at),
    }


async def _conversations(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.created_at))
    ).all()
    result: list[dict[str, Any]] = []
    for conversation in rows:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.user_id == user_id)
                .order_by(Message.created_at)
            )
        ).all()
        result.append(
            {
                "id": str(conversation.id),
                "title": conversation.title,
                "archived": conversation.archived,
                "pinned": conversation.pinned,
                "created_at": _iso(conversation.created_at),
                "updated_at": _iso(conversation.updated_at),
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "status": message.status,
                        "created_at": _iso(message.created_at),
                        "edited_at": _iso(message.edited_at),
                    }
                    for message in messages
                ],
            }
        )
    return result


async def _tasks(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(Task).where(Task.user_id == user_id).order_by(Task.created_at))).all()
    return [
        {
            "id": str(task.id),
            "title": task.title,
            "tool_name": task.tool_name,
            "status": task.status,
            "source": task.source,
            "arguments": task.arguments,
            "result": task.result,
            "error_code": (task.error or {}).get("code") if isinstance(task.error, dict) else None,
            "created_at": _iso(task.created_at),
            "finished_at": _iso(task.finished_at),
        }
        for task in rows
    ]


async def _devices(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(Device).where(Device.user_id == user_id).order_by(Device.created_at))).all()
    return [
        {
            "id": str(device.id),
            "name": device.name,
            "device_type": device.device_type,
            "os": device.os,
            "os_version": device.os_version,
            "pairing_status": device.pairing_status,
            "is_default": device.is_default,
            "capabilities": list(device.capabilities or []),
            "permissions": dict(device.permissions or {}),
            "resource_labels": _resource_labels(dict(device.resources or {})),
            "created_at": _iso(device.created_at),
        }
        for device in rows
    ]


async def _integrations(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(Integration).where(Integration.user_id == user_id).order_by(Integration.provider))).all()
    return [
        {
            "provider": row.provider,
            "status": row.status,
            "account_label": row.account_label,
            "scopes": list(row.scopes or []),
            "last_checked_at": _iso(row.last_checked_at),
            "last_error_code": row.last_error_code,
        }
        for row in rows
    ]


async def _providers(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(ProviderConfig).where(ProviderConfig.user_id == user_id).order_by(ProviderConfig.provider_id))).all()
    return [{"provider_id": row.provider_id, "enabled": row.enabled, "selected_model": row.selected_model} for row in rows]


async def _schedules(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(ScheduledJob).where(ScheduledJob.user_id == user_id).order_by(ScheduledJob.created_at))).all()
    return [
        {
            "id": str(job.id),
            "name": job.name,
            "description": job.description,
            "tool_name": job.tool_name,
            "arguments": job.arguments,
            "schedule_kind": job.schedule_kind,
            "run_at": _iso(job.run_at),
            "time_of_day": job.time_of_day,
            "weekdays": list(job.weekdays or []),
            "timezone": job.timezone,
            "enabled": job.enabled,
            "paused": job.paused,
            "last_run_at": _iso(job.last_run_at),
            "last_status": job.last_status,
        }
        for job in rows
    ]


async def _notifications(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at))).all()
    return [
        {
            "kind": row.kind,
            "severity": row.severity,
            "title": row.title,
            "body": row.body,
            "created_at": _iso(row.created_at),
            "read_at": _iso(row.read_at),
            "dismissed_at": _iso(row.dismissed_at),
        }
        for row in rows
    ]


async def _audit(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(AuditEvent).where(AuditEvent.user_id == user_id).order_by(AuditEvent.created_at))).all()
    return [
        {
            "created_at": _iso(row.created_at),
            "action": row.action,
            "outcome": row.outcome,
            "target_type": row.target_type,
            "target_id": row.target_id,
        }
        for row in rows
    ]


async def build_account_export(db: AsyncSession, user: User) -> dict[str, Any]:
    """The whole account as one JSON-ready dict. Sections are listed in ``sections`` so a reader can check them."""
    settings_document = await load_settings(db, user.id)
    memories_payload = await memory_service.export_payload(db, user_id=user.id)
    sections: dict[str, Any] = {
        "profile": await _profile(user),
        "settings": settings_document.model_dump(mode="json"),
        "conversations": await _conversations(db, user.id),
        "memories": memories_payload["memories"],
        "tasks": await _tasks(db, user.id),
        "devices": await _devices(db, user.id),
        "integrations": await _integrations(db, user.id),
        "providers": await _providers(db, user.id),
        "schedules": await _schedules(db, user.id),
        "notifications": await _notifications(db, user.id),
        "audit": await _audit(db, user.id),
    }
    return {
        "format_version": FORMAT_VERSION,
        "exported_at": utc_now().isoformat().replace("+00:00", "Z"),
        "sections": sorted(sections),
        **sections,
    }
