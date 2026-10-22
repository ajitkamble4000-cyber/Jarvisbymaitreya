"""Aggregates every API router under ``/api/v1``."""

from fastapi import APIRouter

from app.api.routes import (
    account,
    analytics,
    audit,
    auth,
    conversations,
    developer,
    devices,
    events,
    explore,
    health,
    integrations,
    memory,
    metrics,
    users,
    notifications,
    providers,
    schedules,
    search,
    settings,
    tasks,
)

api_router = APIRouter(prefix="/api/v1")
for module in (
    account,
    search,
    health,
    auth,
    settings,
    providers,
    conversations,
    devices,
    tasks,
    schedules,
    memory,
    notifications,
    events,
    audit,
    analytics,
    integrations,
    explore,
    developer,
    users,
    metrics,
):
    api_router.include_router(module.router)
