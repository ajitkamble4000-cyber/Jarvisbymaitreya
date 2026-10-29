"""AI providers API against real PostgreSQL. Upstream calls go through an httpx.MockTransport; nothing leaves the process."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.time import utc_now
from app.models.ai import ProviderConfig, UsageRecord
from app.models.identity import User
from app.models.platform import AuditEvent
from app.providers.base import ProviderError, ProviderErrorCode, TokenUsage
from app.providers.registry import ProviderRegistry
from app.security.passwords import hash_password
from app.services.usage import record_usage
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

API = "/api/v1/providers"
GEMINI_KEY = "AIzaSyINTEG-provider-key-4321"
HF_KEY = "hf_INTEG_token_secret_8765"
SECOND_PASSWORD = "second-user-password"


def _install_mock_upstream(app: Any, handler: Any) -> None:
    """Replaces the registry with one whose HTTP client uses a MockTransport. The app fixture is per test, so this does not leak."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.providers = ProviderRegistry(
        settings=app.state.settings,
        secret_box=app.state.secret_box,
        http=http,
        session_factory=app.state.session_factory,
        events=app.state.events,
    )


def _gemini_models_handler(status: int = 200, body: dict[str, Any] | None = None) -> Any:
    payload = body or {
        "models": [
            {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(status, json=payload)
        return httpx.Response(500, json={})

    return handler


async def _save_credential(client: AsyncClient, provider_id: str, key: str) -> dict[str, Any]:
    response = await client.put(f"{API}/{provider_id}/credential", json={"api_key": key}, headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _add_user(app: Any, email: str, password: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = User(
            email=email,
            display_name="Second user",
            password_hash=hash_password(password),
            role="member",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
    return user_id


async def _login(app: Any, email: str, password: str) -> AsyncClient:
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    )
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


# ---------------------------------------------------------------------------
# Listing, defaults and validation
# ---------------------------------------------------------------------------


async def test_listing_creates_default_rows_once_per_provider(client: AsyncClient, app: Any) -> None:
    await create_owner(client)

    first = await client.get(API)
    second = await client.get(API)
    assert first.status_code == second.status_code == 200

    items = first.json()["items"]
    assert [item["id"] for item in items] == ["gemini", "huggingface", "openrouter"]
    assert [item["display_name"] for item in items] == ["Google Gemini", "Hugging Face Inference", "OpenRouter"]
    for item in items:
        assert item["enabled"] is False
        assert item["configured"] is False
        assert item["credential_hint"] is None
        assert item["selected_model"] is None
        assert item["status"] == "not_configured"
        assert item["timeout_seconds"] == 30.0
        assert item["max_retries"] == 2
        assert item["last_error"] is None
        assert set(item["capabilities"]) == {
            "streaming",
            "tool_calling",
            "multimodal_input",
            "usage_reporting",
            "model_listing",
        }
    assert items[0]["capabilities"]["tool_calling"] is True
    assert items[1]["capabilities"]["tool_calling"] is None
    assert second.json() == first.json()

    async with app.state.session_factory() as db:
        rows = (await db.scalars(select(ProviderConfig))).all()
        assert len(rows) == 3


async def test_unknown_provider_ids_return_404_on_every_route(client: AsyncClient) -> None:
    await create_owner(client)
    headers = csrf_headers(client)
    responses = [
        await client.put(f"{API}/openai", json={"enabled": False}, headers=headers),
        await client.put(f"{API}/openai/credential", json={"api_key": "sk-test-value-1234"}, headers=headers),
        await client.delete(f"{API}/openai/credential", headers=headers),
        await client.post(f"{API}/openai/test", headers=headers),
        await client.get(f"{API}/openai/models"),
    ]
    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "unknown_provider"


async def test_credential_is_encrypted_hinted_and_never_returned(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    key = "AIzaSyCRED-never-returned-abcd"

    response = await client.put(
        f"{API}/gemini/credential",
        json={"api_key": f"  {key}\n"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["credential_hint"] == "…abcd"
    assert body["status"] == "untested"
    assert key not in response.text

    listing = await client.get(API)
    assert key not in listing.text
    assert "credential_ciphertext" not in listing.text

    async with app.state.session_factory() as db:
        row = await db.scalar(select(ProviderConfig).where(ProviderConfig.provider_id == "gemini"))
        assert row is not None
        assert row.credential_ciphertext is not None
        assert key not in row.credential_ciphertext
        assert app.state.secret_box.decrypt(row.credential_ciphertext) == key

        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "provider.credential_set"))).all()
        assert len(audit) == 1
        assert key not in json.dumps(audit[0].data)
        assert "api_key" not in audit[0].data


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": ""},
        {"api_key": "   \t  "},
        {"api_key": "AIza-valid-looking\u0001-with-control"},
        {"api_key": "x" * 513},
        {"api_key": 12345},
        {"api_key": "AIza-valid", "extra": "not allowed"},
    ],
)
async def test_invalid_credentials_are_rejected_with_422(client: AsyncClient, payload: dict[str, Any]) -> None:
    await create_owner(client)
    response = await client.put(f"{API}/gemini/credential", json=payload, headers=csrf_headers(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    for value in payload.values():
        if isinstance(value, str) and len(value) > 8:
            assert value not in response.text


async def test_delete_credential_clears_the_secret_and_disables_the_provider(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await _save_credential(client, "gemini", GEMINI_KEY)
    enabled = await client.put(f"{API}/gemini", json={"enabled": True}, headers=csrf_headers(client))
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    response = await client.delete(f"{API}/gemini/credential", headers=csrf_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["credential_hint"] is None
    assert body["enabled"] is False
    assert body["status"] == "not_configured"
    assert GEMINI_KEY not in response.text

    async with app.state.session_factory() as db:
        row = await db.scalar(select(ProviderConfig).where(ProviderConfig.provider_id == "gemini"))
        assert row is not None
        assert row.credential_ciphertext is None
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "provider.credential_deleted"))).all()
        assert len(audit) == 1


async def test_enabling_a_provider_without_a_credential_is_refused(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.put(f"{API}/gemini", json={"enabled": True}, headers=csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_configured"


async def test_update_validates_ranges_and_rejects_unknown_fields(client: AsyncClient) -> None:
    await create_owner(client)
    headers = csrf_headers(client)
    for payload in ({"timeout_seconds": 0}, {"timeout_seconds": 121}, {"max_retries": 6}, {"max_retries": -1}, {"unknown": 1}):
        response = await client.put(f"{API}/gemini", json=payload, headers=headers)
        assert response.status_code == 422, payload

    ok = await client.put(f"{API}/gemini", json={"timeout_seconds": 45, "max_retries": 3}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["timeout_seconds"] == 45
    assert ok.json()["max_retries"] == 3


async def test_selected_model_is_stored_without_validation_when_unconfigured(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.put(
        f"{API}/huggingface",
        json={"selected_model": "org/some-model"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    assert response.json()["selected_model"] == "org/some-model"
    assert response.json()["configured"] is False


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


async def test_models_return_409_without_a_credential(client: AsyncClient) -> None:
    await create_owner(client)
    for provider_id in ("gemini", "huggingface", "openrouter"):
        response = await client.get(f"{API}/{provider_id}/models")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_configured"


async def test_models_are_listed_from_the_live_service_and_recorded(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _install_mock_upstream(app, _gemini_models_handler())
    await _save_credential(client, "gemini", GEMINI_KEY)

    response = await client.get(f"{API}/gemini/models")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert items[1]["supports_streaming"] is True
    assert GEMINI_KEY not in response.text

    async with app.state.session_factory() as db:
        usage = (await db.scalars(select(UsageRecord).where(UsageRecord.operation == "list_models"))).all()
        assert len(usage) == 1
        assert usage[0].success is True


async def test_selected_model_must_come_from_the_live_list(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _install_mock_upstream(app, _gemini_models_handler())
    await _save_credential(client, "gemini", GEMINI_KEY)
    headers = csrf_headers(client)

    rejected = await client.put(f"{API}/gemini", json={"selected_model": "gemini-invented"}, headers=headers)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "model_not_listed"

    accepted = await client.put(f"{API}/gemini", json={"selected_model": "gemini-2.5-pro"}, headers=headers)
    assert accepted.status_code == 200
    assert accepted.json()["selected_model"] == "gemini-2.5-pro"

    cleared = await client.put(f"{API}/gemini", json={"selected_model": None}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["selected_model"] is None


async def test_model_list_failure_is_a_502_with_the_provider_code(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _install_mock_upstream(app, _gemini_models_handler(status=403))
    await _save_credential(client, "gemini", GEMINI_KEY)
    await client.put(f"{API}/gemini", json={"max_retries": 0}, headers=csrf_headers(client))

    response = await client.get(f"{API}/gemini/models")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "auth_failed"
    assert GEMINI_KEY not in response.text

    # The failed call is counted even though the request ends in an error response.
    async with app.state.session_factory() as db:
        failures = (
            await db.scalars(
                select(UsageRecord).where(UsageRecord.operation == "list_models", UsageRecord.success.is_(False))
            )
        ).all()
        assert len(failures) == 1
        assert failures[0].error_code == "auth_failed"


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------


async def test_successful_test_connection_marks_the_provider_connected(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    _install_mock_upstream(app, _gemini_models_handler())
    await _save_credential(client, "gemini", GEMINI_KEY)

    response = await client.post(f"{API}/gemini/test", headers=csrf_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["error_code"] is None
    assert isinstance(body["latency_ms"], int)
    assert body["provider"]["status"] == "connected"
    assert body["provider"]["last_error"] is None
    assert body["provider"]["last_tested_at"] is not None
    assert GEMINI_KEY not in response.text

    async with app.state.session_factory() as db:
        usage = (await db.scalars(select(UsageRecord).where(UsageRecord.operation == "test"))).all()
        assert len(usage) == 1 and usage[0].success is True
        audit = (await db.scalars(select(AuditEvent).where(AuditEvent.action == "provider.tested"))).all()
        assert len(audit) == 1 and audit[0].outcome == "success"


async def test_failed_test_connection_records_the_error_without_the_key(client: AsyncClient, app: Any) -> None:
    await create_owner(client)

    def rejecting(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"key {GEMINI_KEY} is invalid"})

    _install_mock_upstream(app, rejecting)
    await _save_credential(client, "gemini", GEMINI_KEY)

    response = await client.post(f"{API}/gemini/test", headers=csrf_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "auth_failed"
    assert body["provider"]["status"] == "error"
    assert body["provider"]["last_error"]["code"] == "auth_failed"
    assert GEMINI_KEY not in response.text

    listing = await client.get(API)
    assert listing.json()["items"][0]["status"] == "error"
    assert GEMINI_KEY not in listing.text

    async with app.state.session_factory() as db:
        usage = (await db.scalars(select(UsageRecord).where(UsageRecord.operation == "test"))).all()
        assert len(usage) == 1
        assert usage[0].success is False
        assert usage[0].error_code == "auth_failed"


async def test_test_connection_without_credential_reports_not_configured(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    response = await client.post(f"{API}/huggingface/test", headers=csrf_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "not_configured"
    assert body["latency_ms"] is None
    assert body["provider"]["status"] == "not_configured"


async def test_huggingface_test_stores_the_account_label(client: AsyncClient, app: Any) -> None:
    await create_owner(client)

    def whoami(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingface.co":
            return httpx.Response(200, json={"name": "ada-lovelace", "type": "user"})
        return httpx.Response(500, json={})

    _install_mock_upstream(app, whoami)
    await _save_credential(client, "huggingface", HF_KEY)

    response = await client.post(f"{API}/huggingface/test", headers=csrf_headers(client))
    body = response.json()
    assert body["ok"] is True
    assert body["account_label"] == "ada-lovelace"
    assert HF_KEY not in response.text


async def test_build_returns_a_configured_adapter_and_refuses_when_missing(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    registry: ProviderRegistry = app.state.providers

    async with app.state.session_factory() as db:
        with pytest.raises(ProviderError) as info:
            await registry.build(db, owner_id, "gemini")
        assert info.value.code is ProviderErrorCode.NOT_CONFIGURED

    await _save_credential(client, "gemini", GEMINI_KEY)

    async with app.state.session_factory() as db:
        provider, config = await registry.build(db, owner_id, "gemini")
        assert provider.provider_id == "gemini"
        assert config.provider_id == "gemini"
        assert GEMINI_KEY not in repr(provider)


# ---------------------------------------------------------------------------
# Usage statistics
# ---------------------------------------------------------------------------


async def test_usage_statistics_are_isolated_between_users(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    second_id = await _add_user(app, "second@example.com", SECOND_PASSWORD)

    async with app.state.session_factory() as db:
        for latency in (100, 200, 300):
            record_usage(
                db,
                user_id=owner_id,
                provider_id="gemini",
                model="gemini-2.5-flash",
                operation="chat",
                success=True,
                latency_ms=latency,
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15, estimated=False),
            )
        # A zero latency is an unmeasured value and must not pull the average down.
        record_usage(
            db,
            user_id=owner_id,
            provider_id="gemini",
            model="gemini-2.5-flash",
            operation="chat",
            success=True,
            latency_ms=0,
        )
        record_usage(
            db,
            user_id=owner_id,
            provider_id="gemini",
            model="gemini-2.5-flash",
            operation="chat",
            success=True,
            latency_ms=50,
            usage=TokenUsage(input_tokens=4, output_tokens=2, total_tokens=6, estimated=True),
        )
        # The second user's failure must not appear in the owner's numbers.
        record_usage(
            db,
            user_id=second_id,
            provider_id="huggingface",
            model="meta-llama/Llama-3.1-8B-Instruct",
            operation="chat",
            success=False,
            latency_ms=40,
            error_code="rate_limited",
        )
        await db.commit()

    owner_usage = (await client.get(f"{API}/usage?days=30")).json()
    assert owner_usage["totals"]["requests"] == 5
    assert owner_usage["totals"]["successes"] == 5
    assert owner_usage["totals"]["failures"] == 0
    assert owner_usage["totals"]["input_tokens"] == 34
    assert owner_usage["totals"]["total_tokens"] == 51  # 3 x 15 + 6
    assert owner_usage["totals"]["tokens_estimated"] is True
    # Measured latencies are 100, 200, 300 and 50. The zero value is excluded.
    assert owner_usage["avg_latency_ms"] == pytest.approx(162.5)
    assert [row["provider_id"] for row in owner_usage["by_provider"]] == ["gemini"]
    assert owner_usage["by_model"][0]["model"] == "gemini-2.5-flash"
    assert owner_usage["error_frequency"] == []
    assert owner_usage["estimated_cost_usd"] is None
    assert owner_usage["cost_source"] == "unavailable"
    assert owner_usage["period_days"] == 30

    second_client = await _login(app, "second@example.com", SECOND_PASSWORD)
    second_usage = (await second_client.get(f"{API}/usage?days=30")).json()
    assert second_usage["totals"]["requests"] == 1
    assert second_usage["totals"]["failures"] == 1
    assert second_usage["totals"]["input_tokens"] is None  # nothing was measured, so null rather than zero
    assert second_usage["avg_latency_ms"] == 40.0
    assert [row["provider_id"] for row in second_usage["by_provider"]] == ["huggingface"]
    assert second_usage["error_frequency"] == [{"code": "rate_limited", "count": 1}]
    await second_client.aclose()


async def test_usage_window_excludes_old_records_and_validates_days(client: AsyncClient, app: Any) -> None:
    owner = await create_owner(client)
    owner_id = uuid.UUID(owner["id"])
    async with app.state.session_factory() as db:
        db.add(
            UsageRecord(
                user_id=owner_id,
                created_at=utc_now() - timedelta(days=60),
                provider_id="gemini",
                model="gemini-2.5-flash",
                operation="chat",
                success=True,
                latency_ms=999,
                input_tokens=1000,
                output_tokens=0,
                total_tokens=1000,
                tokens_estimated=False,
            )
        )
        await db.commit()

    assert (await client.get(f"{API}/usage?days=30")).json()["totals"]["requests"] == 0
    assert (await client.get(f"{API}/usage?days=90")).json()["totals"]["requests"] == 1
    assert (await client.get(f"{API}/usage")).json()["period_days"] == 30
    assert (await client.get(f"{API}/usage?days=0")).status_code == 422
    assert (await client.get(f"{API}/usage?days=366")).status_code == 422


async def test_provider_configuration_is_isolated_between_users(client: AsyncClient, app: Any) -> None:
    await create_owner(client)
    await _save_credential(client, "gemini", GEMINI_KEY)
    await _add_user(app, "second@example.com", SECOND_PASSWORD)

    second_client = await _login(app, "second@example.com", SECOND_PASSWORD)
    listing = (await second_client.get(API)).json()
    gemini = next(item for item in listing["items"] if item["id"] == "gemini")
    assert gemini["configured"] is False
    assert gemini["credential_hint"] is None
    await second_client.aclose()
