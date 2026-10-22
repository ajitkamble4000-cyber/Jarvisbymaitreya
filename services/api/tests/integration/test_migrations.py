"""Alembic migrations: ``upgrade head`` must build the schema described by the ORM metadata.

Each test creates a throw-away database, runs the migrations against it and drops it again.
The database role needs CREATEDB; the test is skipped (not passed) when it does not have it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.database.base import Base

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get(
    "JARVIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@127.0.0.1:5432/jarvis_test",
)


def _alembic_config(url: URL) -> Config:
    # No ini file: env.py then skips fileConfig() and does not reset the application's loggers.
    config = Config()
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    return config


async def _create_database(admin_url: URL, name: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            # pgvector must be installed in the template by a superuser once (see README). Copying it avoids needing superuser here.
            await conn.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE jarvis_vector_template')
    finally:
        await engine.dispose()


async def _drop_database(admin_url: URL, name: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await engine.dispose()


def _introspect(conn: Connection) -> dict[str, set[str]]:
    inspector = sa_inspect(conn)
    tables = [name for name in inspector.get_table_names() if name != "alembic_version"]
    return {name: {column["name"] for column in inspector.get_columns(name)} for name in tables}


def _metadata_diff(conn: Connection) -> list[object]:
    return compare_metadata(MigrationContext.configure(connection=conn), Base.metadata)


async def _inspect_database(url: URL) -> dict[str, set[str]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_introspect)
    finally:
        await engine.dispose()


async def _diff_database(url: URL) -> list[object]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_metadata_diff)
    finally:
        await engine.dispose()


def _scratch_urls() -> tuple[URL, URL, str]:
    base = make_url(TEST_DATABASE_URL)
    name = f"jarvis_migrate_{uuid.uuid4().hex[:12]}"
    return base.set(database="postgres"), base.set(database=name), name


def test_upgrade_head_matches_metadata_and_downgrade_base_is_clean() -> None:
    admin_url, target_url, name = _scratch_urls()
    try:
        asyncio.run(_create_database(admin_url, name))
    except (DBAPIError, ProgrammingError) as exc:  # pragma: no cover - depends on the role's privileges
        pytest.skip(f"cannot create a scratch database for the migration check: {exc.__class__.__name__}")

    try:
        config = _alembic_config(target_url)
        command.upgrade(config, "head")

        expected = {table.name: {column.name for column in table.columns} for table in Base.metadata.sorted_tables}
        assert asyncio.run(_inspect_database(target_url)) == expected
        # Table and column names match; the full autogenerate comparison (types, nullability, indexes,
        # constraints) must also report no differences.
        assert asyncio.run(_diff_database(target_url)) == []

        command.downgrade(config, "base")
        assert asyncio.run(_inspect_database(target_url)) == {}
    finally:
        asyncio.run(_drop_database(admin_url, name))
