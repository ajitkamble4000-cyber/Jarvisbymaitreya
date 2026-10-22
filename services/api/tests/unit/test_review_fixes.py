"""Regression tests for the review findings in the rate limiter, configuration, explore, memory, chat, plugins,
integration tools and the proxy trust settings. Each test names the behaviour it protects."""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import ConfigurationError, Settings
from app.integrations import tools as integration_tools
from app.integrations.oauth.providers import GOOGLE_CALENDAR, credentials
from app.integrations.tools import CALENDAR_TOOL, GITHUB_TOOL, integration_tool_specs
from app.plugins.loader import PLUGIN_CATEGORIES, _check_handler_names, _check_tool_names, _Plugin
from app.security.ratelimit import RateLimiter
from app.services.chat import _late_result_text, outcome_texts
from app.services.explore_service import (
    EXPLORE_CATEGORIES,
    ExploreContext,
    _TOOL_CATEGORY_MAP,
    _tool_item,
)
from app.services.memory_service import EmbeddingUnavailable, _embed_texts, find_sensitive_reason
from app.tools.catalog import TOOL_SPECS
from app.tools.registry import build_tool_registry
from app.tools.server_handlers import get_server_handler, register_server_handler

REPO_ROOT = Path(__file__).resolve().parents[4]


# -- rate limiter -----------------------------------------------------------------------------


def test_rate_limiter_does_not_rebuild_its_table_on_every_hit_when_full() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    original = limiter._sweep
    sweeps = 0

    def counted(at: float) -> None:
        nonlocal sweeps
        sweeps += 1
        original(at)

    limiter._sweep = counted  # type: ignore[method-assign]
    # 60,000 distinct keys inside one window: far more than the 50,000 tracked keys.
    for index in range(60_000):
        limiter.hit(f"client-{index}", 5)
    # Batched eviction keeps the number of full rebuilds small (a rebuild on every hit would be 10,000+ here).
    assert sweeps <= 10


def test_rate_limiter_still_limits_a_key_after_the_table_was_trimmed() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    for index in range(55_000):
        limiter.hit(f"spoofed-{index}", 1)
    assert limiter.hit("victim", 2)
    assert limiter.hit("victim", 2)
    assert not limiter.hit("victim", 2)


# -- configuration ----------------------------------------------------------------------------


def test_oauth_secrets_and_the_encryption_key_do_not_appear_in_repr_or_dump() -> None:
    settings = Settings(
        jarvis_env="development",
        encryption_key="enc-key-value-0123456789abcdef",
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-secret-value-1234",
        github_oauth_client_id="github-client-id",
        github_oauth_client_secret="github-secret-value-1234",
    )
    rendered = repr(settings) + str(settings.model_dump()) + str(settings.model_dump(mode="json"))
    for secret in (
        "enc-key-value-0123456789abcdef",
        "google-secret-value-1234",
        "github-secret-value-1234",
    ):
        assert secret not in rendered


def test_the_client_secret_is_still_available_to_the_provider_code() -> None:
    settings = Settings(
        jarvis_env="development",
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-secret-value-1234",
    )
    assert credentials(settings, GOOGLE_CALENDAR) == ("google-client-id", "google-secret-value-1234")


def test_embedding_dimensions_are_fixed_to_the_memory_column(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(jarvis_env="development").embedding_dimensions == 768
    # The documented value arrives as a string from the environment or .env and must still be accepted.
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "768")
    assert Settings(jarvis_env="development").embedding_dimensions == 768
    with pytest.raises(ValidationError):
        Settings(jarvis_env="development", embedding_dimensions=1024)


# -- explore ----------------------------------------------------------------------------------


def test_every_plugin_category_is_listed_in_an_explore_category() -> None:
    for category in PLUGIN_CATEGORIES:
        mapped = _TOOL_CATEGORY_MAP.get(category)
        assert mapped in EXPLORE_CATEGORIES, category
    assert _TOOL_CATEGORY_MAP["productivity"] == "Productivity"
    assert _TOOL_CATEGORY_MAP["development"] == "Development"


def _context(connected: frozenset[str] | None) -> ExploreContext:
    return ExploreContext(
        has_paired_device=False,
        provider_configured=False,
        background_workers=True,
        disabled_tools=frozenset(),
        connected_integrations=connected,
    )


def test_an_integration_tool_is_not_working_until_its_provider_is_connected() -> None:
    settings = Settings(
        jarvis_env="development",
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-secret-value-1234",
        github_oauth_client_id="github-client-id",
        github_oauth_client_secret="github-secret-value-1234",
    )
    specs = {spec.name: spec for spec in integration_tool_specs(settings, None)}

    calendar = _tool_item(specs[CALENDAR_TOOL], _context(frozenset()))
    assert calendar.status == "requires_configuration"
    assert any("Connect Google Calendar" in line for line in calendar.requirements)
    assert calendar.category == "Productivity"

    assert _tool_item(specs[CALENDAR_TOOL], _context(frozenset({"google_calendar"}))).status == "working"
    github = _tool_item(specs[GITHUB_TOOL], _context(frozenset({"google_calendar"})))
    assert github.status == "requires_configuration"
    assert github.category == "Development"
    # Without connection state (None) the check is left out, as before.
    assert _tool_item(specs[CALENDAR_TOOL], _context(None)).status == "working"


# -- memory -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "my github token is ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2",
        "fine-grained github_pat_" + "11ABCDEFGH0123456789abcd",
        "aws key AKIAIOSFODNN7EXAMPLE",
        "slack xoxb-1234567890-abcdefghijk",
        "stripe sk_live_abcdefgh12345678",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_common_credential_formats_are_refused(text: str) -> None:
    assert find_sensitive_reason(text) is not None


def test_ordinary_memory_text_is_not_refused() -> None:
    assert find_sensitive_reason("Dentist appointment on Friday at 3pm, bring the insurance card") is None


async def test_a_non_finite_vector_is_unavailable_instead_of_breaking_the_save() -> None:
    class NonFiniteProvider:
        async def embed(self, texts: list[str], *, model: str, dimensions: int, timeout_seconds: float) -> list[list[float]]:
            return [[float("nan")] * 768 for _ in texts]

    async def build(db: Any, user_id: Any, provider_id: str) -> tuple[NonFiniteProvider, Any]:
        return NonFiniteProvider(), SimpleNamespace(timeout_seconds=5)

    source = SimpleNamespace(model="gemini-embedding", dimensions=768, providers=SimpleNamespace(build=build))
    with pytest.raises(EmbeddingUnavailable) as caught:
        await _embed_texts(None, uuid.uuid4(), source, ["hello"])  # type: ignore[arg-type]
    assert caught.value.code == "bad_embedding_values"


# -- chat wording -----------------------------------------------------------------------------


def _failed_task(error: dict[str, Any], started: bool) -> SimpleNamespace:
    return SimpleNamespace(
        title="Open YouTube on Laptop",
        status="failed",
        tool_name="open_url",
        result=None,
        error=error,
        started_at=object() if started else None,
    )


def test_a_running_task_that_timed_out_is_not_announced_as_failed() -> None:
    task = _failed_task({"code": "timeout", "message": "The device did not report a result in time.", "details": {}}, True)
    _, reply = outcome_texts(task)
    assert reply == "Not confirmed yet: Open YouTube on Laptop. The device did not report a result in time."


def test_a_device_failure_is_still_announced_as_failed() -> None:
    task = _failed_task({"code": "execution_failed", "message": "Chrome is not installed.", "details": {}}, True)
    assert outcome_texts(task)[1] == "It did not work: Open YouTube on Laptop. Chrome is not installed."


def test_a_late_unverified_success_is_not_called_finished() -> None:
    text = _late_result_text("timeout", "Started Chrome (pid 4313)", unconfirmed=True)
    assert text == (
        "The earlier request finished after it timed out, but the result is not confirmed: Started Chrome (pid 4313)."
    )
    assert _late_result_text("timeout", "Opened YouTube.") == (
        "The earlier request finished after it timed out: Opened YouTube."
    )


# -- plugins and integration tools ------------------------------------------------------------


def test_a_plugin_cannot_claim_an_integration_tool_name() -> None:
    renamed = dataclasses.replace(TOOL_SPECS[0], name=CALENDAR_TOOL)
    plugin = _Plugin(name="probe", version="1.0.0", specs=(renamed,), handlers={})
    with pytest.raises(ConfigurationError):
        _check_tool_names([plugin])


def test_a_plugin_handler_clash_is_refused_before_anything_is_registered() -> None:
    async def first(db: Any, user_id: Any, args: Any) -> dict:
        return {}

    async def second(db: Any, user_id: Any, args: Any) -> dict:
        return {}

    register_server_handler("review_probe_tool", first)
    plugin = _Plugin(
        name="probe",
        version="1.0.0",
        specs=(),
        handlers={"review_probe_tool": second, "review_probe_other": second},
    )
    with pytest.raises(ConfigurationError):
        _check_handler_names([plugin])
    # The second handler of the plugin was not registered either.
    assert get_server_handler("review_probe_other") is None
    assert get_server_handler("review_probe_tool") is first


def test_building_a_registry_without_an_http_client_keeps_the_app_binding() -> None:
    settings = Settings(
        jarvis_env="development",
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-secret-value-1234",
    )
    app_http = httpx.AsyncClient()
    build_tool_registry(settings, app_http)
    build_tool_registry(settings, None)
    assert integration_tools._BINDING.http is app_http


# -- proxy trust ------------------------------------------------------------------------------


def test_forwarded_headers_are_trusted_only_from_the_web_proxy() -> None:
    dockerfile = REPO_ROOT / "services/api/Dockerfile"
    nginx = REPO_ROOT / "apps/web/nginx.conf"
    compose = REPO_ROOT / "docker-compose.yml"
    if not (dockerfile.exists() and nginx.exists() and compose.exists()):
        pytest.skip("deployment files are not part of this checkout")
    assert "--forwarded-allow-ips='*'" not in dockerfile.read_text(encoding="utf-8")
    assert "--forwarded-allow-ips=" in dockerfile.read_text(encoding="utf-8")
    nginx_text = nginx.read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx_text
    assert "proxy_add_x_forwarded_for" not in nginx_text
    compose_text = compose.read_text(encoding="utf-8")
    assert "ipv4_address: 172.30.0.10" in compose_text
    assert "FORWARDED_ALLOW_IPS: 172.30.0.10" in compose_text


async def test_a_spoofed_forwarded_for_from_an_untrusted_peer_is_ignored() -> None:
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["client"] = scope["client"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        return None

    middleware = ProxyHeadersMiddleware(app, trusted_hosts="172.30.0.10")
    # A client that reaches the API directly (not through nginx) cannot choose its own address.
    await middleware(
        {"type": "http", "client": ("203.0.113.5", 4000), "headers": [(b"x-forwarded-for", b"198.51.100.7")]},
        receive,
        send,
    )
    assert seen["client"] == ("203.0.113.5", 4000)
    # The web container, which replaces the header with the real client address, is trusted.
    await middleware(
        {"type": "http", "client": ("172.30.0.10", 4000), "headers": [(b"x-forwarded-for", b"198.51.100.7")]},
        receive,
        send,
    )
    assert seen["client"][0] == "198.51.100.7"
