"""Quiet hours in the notification settings: defaults, complete windows, clock format and the PATCH merge rule."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import create_owner, csrf_headers

BASE = "/api/v1/settings"


async def test_quiet_hours_are_off_by_default_and_read_back(client: AsyncClient) -> None:
    await create_owner(client)
    notifications = (await client.get(BASE)).json()["notifications"]
    assert notifications["quiet_hours_enabled"] is False
    assert notifications["quiet_hours_start"] is None
    assert notifications["quiet_hours_end"] is None


async def test_a_complete_overnight_window_is_saved(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.patch(
        BASE,
        json={"notifications": {"quiet_hours_enabled": True, "quiet_hours_start": "22:30", "quiet_hours_end": "07:00"}},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    saved = response.json()["notifications"]
    assert (saved["quiet_hours_enabled"], saved["quiet_hours_start"], saved["quiet_hours_end"]) == (True, "22:30", "07:00")


async def test_turning_quiet_hours_on_needs_both_times_in_the_merged_document(client: AsyncClient) -> None:
    await create_owner(client)
    refused = await client.patch(BASE, json={"notifications": {"quiet_hours_enabled": True}}, headers=csrf_headers(client))
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "validation_error"

    # The window can come in a later patch, and it is checked together with the stored switch.
    await client.patch(BASE, json={"notifications": {"quiet_hours_start": "21:00"}}, headers=csrf_headers(client))
    accepted = await client.patch(
        BASE, json={"notifications": {"quiet_hours_enabled": True, "quiet_hours_end": "06:00"}}, headers=csrf_headers(client)
    )
    assert accepted.status_code == 200, accepted.text


async def test_equal_start_and_end_are_refused(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.patch(
        BASE,
        json={"notifications": {"quiet_hours_enabled": True, "quiet_hours_start": "08:00", "quiet_hours_end": "08:00"}},
        headers=csrf_headers(client),
    )
    assert response.status_code == 422


async def test_malformed_clock_values_are_refused(client: AsyncClient) -> None:
    await create_owner(client)
    bad_values: list[Any] = ["24:00", "7:00", "12:60", "noon", "", "12:00:00", 1200]
    for value in bad_values:
        response = await client.patch(
            BASE, json={"notifications": {"quiet_hours_start": value}}, headers=csrf_headers(client)
        )
        assert response.status_code == 422, (value, response.text)
