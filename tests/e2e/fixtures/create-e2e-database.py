"""Recreates the throw-away database used by the milestone end-to-end test, then migrates nothing.

The backend start script runs ``alembic upgrade head`` on this empty database. Run with the backend
venv: services/api/.venv/bin/python tests/e2e/fixtures/create-e2e-database.py [--drop]
The database name is fixed to jarvis_e2e so the dev database is never touched.
"""

from __future__ import annotations

import asyncio
import sys

import asyncpg

ADMIN_DSN = "postgresql://jarvis:jarvis@127.0.0.1:5432/postgres"
NAME = "jarvis_e2e"


async def main() -> None:
    drop_only = "--drop" in sys.argv
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{NAME}" WITH (FORCE)')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{NAME}" OWNER jarvis TEMPLATE jarvis_vector_template')
    finally:
        await conn.close()
    print(("dropped " if drop_only else "created ") + NAME)


if __name__ == "__main__":
    asyncio.run(main())
