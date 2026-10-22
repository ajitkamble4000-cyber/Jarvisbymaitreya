"""Application factory. Builds every process-wide service once and wires it into ``app.state``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.deps import CSRF_HEADER
from app.api.errors import register_error_handlers
from app.api.middleware import ApiSecurityMiddleware
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.database.session import create_db_engine, create_session_factory
from app.devices.hub import DeviceHub
from app.providers.registry import ProviderRegistry
from app.scheduler.worker import Scheduler
from app.security.crypto import build_secret_box
from app.security.ratelimit import RateLimiter
from app.services.events import EventBus
from app.services.task_engine import TaskEngine
from app.tools.registry import build_tool_registry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    secret_box = build_secret_box(settings)
    events = EventBus()
    rate_limiter = RateLimiter()
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.provider_default_timeout_seconds),
        headers={"User-Agent": f"jarvis-ai/{__version__}"},
    )
    tools = build_tool_registry(settings, http)

    providers = ProviderRegistry(settings=settings, secret_box=secret_box, http=http, session_factory=session_factory, events=events)
    devices = DeviceHub(settings=settings, session_factory=session_factory, secret_box=secret_box, events=events)
    tasks = TaskEngine(
        settings=settings,
        session_factory=session_factory,
        secret_box=secret_box,
        events=events,
        tools=tools,
        devices=devices,
    )
    devices.bind(tasks)
    scheduler = Scheduler(settings=settings, session_factory=session_factory, tasks=tasks, events=events)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.run_background_workers:
            await devices.start()
            # The task sweeps enforce queue TTL, approval expiry and running-task timeouts. Without them
            # those limits are not enforced at all.
            await tasks.start()
            await scheduler.start()
        try:
            yield
        finally:
            if settings.run_background_workers:
                await scheduler.stop()
                await tasks.stop()
                await devices.stop()
            await http.aclose()
            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        summary="JARVIS AI backend API",
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.secret_box = secret_box
    app.state.events = events
    app.state.rate_limiter = rate_limiter
    app.state.tools = tools
    app.state.providers = providers
    app.state.device_hub = devices
    app.state.task_engine = tasks
    app.state.scheduler = scheduler
    app.state.http = http

    register_error_handlers(app)
    app.add_middleware(ApiSecurityMiddleware, settings=settings, rate_limiter=rate_limiter)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", CSRF_HEADER, "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.include_router(api_router)
    return app

