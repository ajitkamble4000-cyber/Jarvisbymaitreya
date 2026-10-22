"""Plugins against PostgreSQL: registry contents, the /tools route, and both shipped tools run through the task engine.

Each test builds its own app from settings that name the plugins it needs. Plugin code runs in-process, so these
tests need no network and no provider. Cross-user checks cover the clock, which reads the caller's settings.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import ConfigurationError, Settings
from app.core.time import utc_now
from app.database.base import Base
from app.main import create_app
from app.models.identity import User, UserPreference
from app.models.tasks import Task
from app.services.task_engine import TaskEngine
from app.services.task_states import ProposalRefusedError
from app.tools.registry import build_tool_registry
from tests.conftest import TEST_ORIGIN, create_owner

CLOCK_TOOL = "current_time"
CALC_TOOL = "calculate"
BRIEFING_TOOL = "daily_briefing"


@pytest.fixture
def plugins_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"enabled_plugins": ["clock", "calculator"]})


@pytest_asyncio.fixture(loop_scope="session")
async def plugins_app(plugins_settings: Settings, schema: None) -> AsyncIterator[Any]:
    application = create_app(plugins_settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        yield application


@pytest_asyncio.fixture(loop_scope="session")
async def plugins_client(plugins_app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=plugins_app),
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    ) as http:
        yield http


async def _add_user(app: Any, email: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(email=email, display_name=email.split("@")[0], password_hash="!unused", is_active=True)
        db.add(user)
        await db.commit()
        return user.id


async def _set_timezone(app: Any, user_id: uuid.UUID, zone: str) -> None:
    now = utc_now()
    async with app.state.session_factory() as db:
        db.add(
            UserPreference(
                user_id=user_id,
                settings={"timezone": zone},
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()


async def _propose(app: Any, user_id: uuid.UUID, tool: str, arguments: dict[str, Any]) -> Task:
    async with app.state.session_factory() as db:
        task = await app.state.task_engine.propose(
            db, user_id=user_id, tool_name=tool, arguments=arguments, source="chat"
        )
        await db.commit()
        return task


async def _task_count(app: Any, user_id: uuid.UUID) -> int:
    async with app.state.session_factory() as db:
        return int(await db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user_id)) or 0)


# -- registry ---------------------------------------------------------------------------------


def test_registry_contains_both_plugin_tools_when_enabled(plugins_settings: Settings) -> None:
    names = {spec.name for spec in build_tool_registry(plugins_settings).all()}
    assert CLOCK_TOOL in names
    assert CALC_TOOL in names


def test_registry_omits_plugin_tools_when_none_are_enabled(settings: Settings) -> None:
    names = {spec.name for spec in build_tool_registry(settings.model_copy(update={"enabled_plugins": []})).all()}
    assert CLOCK_TOOL not in names
    assert CALC_TOOL not in names


def test_only_the_enabled_plugin_contributes_its_tool(settings: Settings) -> None:
    names = {spec.name for spec in build_tool_registry(settings.model_copy(update={"enabled_plugins": ["clock"]})).all()}
    assert CLOCK_TOOL in names
    assert CALC_TOOL not in names


def test_unknown_plugin_name_fails_the_build(settings: Settings) -> None:
    with pytest.raises(ConfigurationError) as raised:
        build_tool_registry(settings.model_copy(update={"enabled_plugins": ["clock", "shell"]}))
    assert "shell" in str(raised.value)
    assert "calculator" in str(raised.value)


def test_unknown_plugin_name_stops_app_startup(settings: Settings) -> None:
    with pytest.raises(ConfigurationError):
        create_app(settings.model_copy(update={"enabled_plugins": ["not_a_plugin"]}))


# -- /tools route -----------------------------------------------------------------------------


async def test_tools_route_lists_enabled_plugin_tools(plugins_client: AsyncClient) -> None:
    await create_owner(plugins_client)
    items = {item["name"]: item for item in (await plugins_client.get("/api/v1/tools")).json()["items"]}
    assert CLOCK_TOOL in items
    assert CALC_TOOL in items
    for name in (CLOCK_TOOL, CALC_TOOL):
        assert items[name]["risk_level"] == "low"
        assert items[name]["approval_required"] is False
        assert items[name]["target"] == "server"
        assert items[name]["category"] == "productivity"
        assert items[name]["status"] == "working"


async def test_tools_route_lists_the_default_plugin_tools(client: AsyncClient, app: Any) -> None:
    assert app.state.settings.enabled_plugins == ["clock", "briefing", "calculator"]
    await create_owner(client)
    names = {item["name"] for item in (await client.get("/api/v1/tools")).json()["items"]}
    assert {CLOCK_TOOL, BRIEFING_TOOL, CALC_TOOL} <= names


# -- through the task engine ------------------------------------------------------------------


async def test_clock_and_calculator_run_through_the_task_engine(plugins_app: Any) -> None:
    user_id = await _add_user(plugins_app, "runner@example.com")

    clock = await _propose(plugins_app, user_id, CLOCK_TOOL, {"timezone": "Asia/Tokyo"})
    assert clock.status == "completed"
    assert clock.tool_name == CLOCK_TOOL
    output = clock.result["output"]
    assert output["timezone"] == "Asia/Tokyo"
    assert output["utc_offset"] == "+09:00"
    assert output["local_time"].endswith("+09:00")
    assert clock.result["summary"]

    calc = await _propose(plugins_app, user_id, CALC_TOOL, {"expression": "(2 + 3) * 4 - 7 // 2"})
    assert calc.status == "completed"
    assert calc.result["output"]["result"] == 17

    failed = await _propose(plugins_app, user_id, CALC_TOOL, {"expression": "1 / 0"})
    assert failed.status == "failed"
    assert failed.error["code"] == "division_by_zero"


async def test_clock_without_a_zone_uses_the_callers_settings_then_utc(plugins_app: Any) -> None:
    owner = await _add_user(plugins_app, "tokyo@example.com")
    other = await _add_user(plugins_app, "no-settings@example.com")
    await _set_timezone(plugins_app, owner, "Asia/Tokyo")

    mine = await _propose(plugins_app, owner, CLOCK_TOOL, {})
    assert mine.status == "completed"
    assert mine.result["output"]["timezone"] == "Asia/Tokyo"

    theirs = await _propose(plugins_app, other, CLOCK_TOOL, {})
    assert theirs.status == "completed"
    assert theirs.result["output"]["timezone"] == "UTC"
    assert theirs.result["output"]["utc_offset"] == "+00:00"
    assert "Tokyo" not in str(theirs.result), "one user's settings must never reach another user's result"


async def test_clock_result_time_is_the_current_time(plugins_app: Any) -> None:
    user_id = await _add_user(plugins_app, "now@example.com")
    before = utc_now()
    task = await _propose(plugins_app, user_id, CLOCK_TOOL, {"timezone": "UTC"})
    after = utc_now()
    reported = datetime.fromisoformat(task.result["output"]["local_time"])
    assert before.replace(microsecond=0) <= reported <= after


async def test_clock_refuses_an_unknown_zone_and_creates_no_task(plugins_app: Any) -> None:
    user_id = await _add_user(plugins_app, "badzone@example.com")
    with pytest.raises(ProposalRefusedError) as raised:
        await _propose(plugins_app, user_id, CLOCK_TOOL, {"timezone": "Mars/Olympus"})
    assert raised.value.code == "invalid_arguments"
    assert await _task_count(plugins_app, user_id) == 0


async def test_calculator_refuses_code_and_creates_no_task(plugins_app: Any) -> None:
    user_id = await _add_user(plugins_app, "injector@example.com")
    for expression in ("__import__('os').system('id')", "open('/etc/passwd').read()", "().__class__"):
        with pytest.raises(ProposalRefusedError) as raised:
            await _propose(plugins_app, user_id, CALC_TOOL, {"expression": expression})
        assert raised.value.code == "invalid_arguments"
    assert await _task_count(plugins_app, user_id) == 0


async def test_an_engine_without_the_calculator_refuses_its_tool(plugins_app: Any, settings: Settings) -> None:
    clock_only = build_tool_registry(settings.model_copy(update={"enabled_plugins": ["clock"]}))
    engine = TaskEngine(
        settings=settings,
        session_factory=plugins_app.state.session_factory,
        secret_box=plugins_app.state.secret_box,
        events=plugins_app.state.events,
        tools=clock_only,
        devices=plugins_app.state.device_hub,
    )
    user_id = await _add_user(plugins_app, "disabled@example.com")
    async with plugins_app.state.session_factory() as db:
        with pytest.raises(ProposalRefusedError):
            await engine.propose(db, user_id=user_id, tool_name=CALC_TOOL, arguments={"expression": "1 + 1"}, source="chat")
    assert await _task_count(plugins_app, user_id) == 0
