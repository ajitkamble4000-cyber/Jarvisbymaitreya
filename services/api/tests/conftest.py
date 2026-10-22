"""Shared test fixtures.

Integration tests run against a real PostgreSQL server. Point JARVIS_TEST_DATABASE_URL at a
disposable database; the schema is dropped and recreated once per test session and every
table is truncated between tests.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

# Tests run with test-environment rules. Production defaults would otherwise demand an encryption key.
os.environ.setdefault("JARVIS_ENV", "test")

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.database.base import Base
from app.main import create_app
import app.models  # noqa: F401  - registers every table on Base.metadata

TEST_DATABASE_URL = os.environ.get(
    "JARVIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@127.0.0.1:5432/jarvis_test",
)
TEST_ORIGIN = "http://localhost:5173"


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    data_dir: Path = tmp_path_factory.mktemp("data")
    return Settings(
        jarvis_env="test",
        database_url=TEST_DATABASE_URL,
        data_dir=data_dir,
        encryption_key=Fernet.generate_key().decode("ascii"),
        public_base_url=TEST_ORIGIN,
        allowed_origins=[TEST_ORIGIN],
        run_background_workers=False,
        login_rate_limit_per_minute=10_000,
        api_rate_limit_per_minute=100_000,
        cookie_secure=False,
        log_level="WARNING",
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def schema(settings: Settings) -> AsyncIterator[None]:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def app(settings: Settings, schema: None) -> AsyncIterator[object]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        yield application


@pytest_asyncio.fixture(loop_scope="session")
async def client(app: object) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    ) as http:
        yield http


def csrf_headers(client: AsyncClient) -> dict[str, str]:
    """Headers a browser would send on an unsafe request after the CSRF cookie is set."""
    token = client.cookies.get("jarvis_csrf")
    return {"X-CSRF-Token": token} if token else {}


async def create_owner(client: AsyncClient, email: str = "owner@example.com", password: str = "correct-horse-battery") -> dict:
    response = await client.post(
        "/api/v1/auth/setup",
        json={"email": email, "password": password, "display_name": "Owner"},
        headers=csrf_headers(client) or {},
    )
    assert response.status_code == 201, response.text
    return response.json()
