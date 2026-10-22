"""GET /api/v1/tasks/export.csv: owner scoping, filters, headers and the declared route order."""

from __future__ import annotations

import csv
import io
from typing import Any

from httpx import AsyncClient

from app.services.task_export import COLUMNS
from tests.conftest import create_owner, csrf_headers
from tests.integration.test_tasks_policy import build_harness, propose

EXPORT = "/api/v1/tasks/export.csv"


async def _rows(response: Any) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.text)))


async def test_owner_exports_own_tasks_with_headers(client: AsyncClient) -> None:
    await create_owner(client)
    for _ in range(2):
        created = await client.post("/api/v1/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client))
        assert created.status_code == 201, created.text

    response = await client.get(EXPORT)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="jarvis-tasks.csv"'
    assert response.headers["cache-control"] == "no-store"
    rows = await _rows(response)
    assert tuple(rows[0]) == COLUMNS
    assert len(rows) == 3
    status_index = COLUMNS.index("status")
    assert {row[status_index] for row in rows[1:]} == {"completed"}


async def test_export_is_scoped_to_the_owner(client: AsyncClient, app: Any, settings: Any) -> None:
    await create_owner(client)
    harness = await build_harness(app, settings)
    foreign = await propose(harness, "list_devices", {})
    await client.post("/api/v1/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client))

    rows = await _rows(await client.get(EXPORT))

    ids = [row[COLUMNS.index("id")] for row in rows[1:]]
    assert len(ids) == 1
    assert str(foreign.id) not in ids


async def test_filters_match_the_list_endpoint(client: AsyncClient) -> None:
    await create_owner(client)
    await client.post("/api/v1/tasks", json={"tool_name": "list_devices", "arguments": {}}, headers=csrf_headers(client))

    assert len(await _rows(await client.get(f"{EXPORT}?status=completed"))) == 2
    assert len(await _rows(await client.get(f"{EXPORT}?status=failed"))) == 1
    assert len(await _rows(await client.get(f"{EXPORT}?q=devices"))) == 2
    assert len(await _rows(await client.get(f"{EXPORT}?q=no-such-title"))) == 1


async def test_export_path_is_not_read_as_a_task_id(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.get(EXPORT)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


async def test_export_refuses_anonymous_clients(client: AsyncClient) -> None:
    response = await client.get(EXPORT)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
