"""GET /api/v1/audit/export.csv: the owner's trail as CSV, with no IP address and no event data."""

from __future__ import annotations

import csv
import io

from httpx import AsyncClient

from tests.conftest import create_owner, csrf_headers

API = "/api/v1"


async def test_audit_export_has_the_fixed_columns_and_leaves_out_ip_and_data(client: AsyncClient) -> None:
    await create_owner(client)
    await client.post(f"{API}/conversations", json={"title": "Audited"}, headers=csrf_headers(client))

    response = await client.get(f"{API}/audit/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="jarvis-audit.csv"'
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["created_at", "actor_type", "actor_id", "action", "target_type", "target_id", "outcome"]
    assert len(rows) > 1
    assert "ip_address" not in response.text
    assert "127.0.0.1" not in response.text and "testclient" not in response.text


async def test_audit_export_filters_match_the_list(client: AsyncClient) -> None:
    await create_owner(client)
    await client.post(f"{API}/conversations", json={"title": "Audited"}, headers=csrf_headers(client))
    listing = (await client.get(f"{API}/audit", params={"outcome": "success", "limit": 200})).json()
    exported = list(csv.reader(io.StringIO((await client.get(f"{API}/audit/export.csv", params={"outcome": "success"})).text)))
    assert len(exported) - 1 == listing["total"]


async def test_audit_export_requires_a_session(client: AsyncClient) -> None:
    response = await client.get(f"{API}/audit/export.csv")
    assert response.status_code == 401
