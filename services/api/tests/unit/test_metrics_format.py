"""Exposition escaping, route labels, histogram buckets, counters, and request recording with a fake clock."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.api import middleware as middleware_module
from app.api.middleware import ApiSecurityMiddleware
from app.core.config import Settings
from app.security.ratelimit import RateLimiter
from app.services.metrics import RequestMetrics, Sample, escape_label_value, format_sample, route_label

TASK_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
TASK_TEMPLATE = "/api/v1/tasks/{task_id}"


# -- exposition escaping -------------------------------------------------------


def test_escape_label_value_escapes_backslash_quote_and_newline() -> None:
    assert escape_label_value('a\\b"c\nd') == 'a\\\\b\\"c\\nd'


def test_format_sample_escapes_values_and_omits_empty_braces() -> None:
    assert format_sample(Sample("m", (("route", 'x"y\\z'),), 2.0)) == 'm{route="x\\"y\\\\z"} 2'
    assert format_sample(Sample("g", (), 1.0)) == "g 1"


# -- route labels --------------------------------------------------------------


def test_route_label_masks_parameters_uuids_and_numbers() -> None:
    assert route_label(f"/api/v1/tasks/{TASK_ID}", TASK_TEMPLATE) == "/api/v1/tasks/{id}"
    assert route_label("/api/v1/items/42/edit", "/api/v1/items/42/edit") == "/api/v1/items/{id}/edit"
    assert (
        route_label("/api/v1/providers/openai/models", "/api/v1/providers/{provider_id}/models")
        == "/api/v1/providers/{id}/models"
    )


def test_route_label_drops_the_query_string() -> None:
    label = route_label(f"/api/v1/tasks/{TASK_ID}?user=owner@example.com", TASK_TEMPLATE)
    assert label == "/api/v1/tasks/{id}"


def test_route_label_is_unmatched_without_a_template_or_on_a_shape_mismatch() -> None:
    assert route_label("/api/v1/secret-42", None) == "unmatched"
    assert route_label("/api/v1/tasks/a/b", TASK_TEMPLATE) == "unmatched"


# -- histogram and counter -----------------------------------------------------


def _bucket_values(lines: list[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in lines:
        if "_bucket{" in line:
            bound = line.split('le="')[1].split('"')[0]
            values[bound] = float(line.rsplit(" ", 1)[1])
    return values


def test_histogram_buckets_are_cumulative_and_inclusive() -> None:
    metrics = RequestMetrics()
    for duration in (0.001, 0.005, 0.2, 10.0, 11.0):
        metrics.observe("GET", "/api/v1/health", 200, duration)
    lines = metrics.render()

    buckets = _bucket_values(lines)
    assert buckets["0.005"] == 2  # a duration equal to a bound is inside that bucket
    assert buckets["0.1"] == 2
    assert buckets["0.25"] == 3
    assert buckets["10.0"] == 4
    assert buckets["+Inf"] == 5
    assert 'jarvis_http_request_duration_seconds_count{method="GET",route="/api/v1/health"} 5' in lines
    sum_line = next(line for line in lines if "_sum{" in line)
    assert float(sum_line.rsplit(" ", 1)[1]) == pytest.approx(21.206)


def test_counter_is_per_method_route_and_status_and_collapses_unknown_methods() -> None:
    metrics = RequestMetrics()
    metrics.observe("GET", "/api/v1/tasks/{id}", 200, 0.01)
    metrics.observe("GET", "/api/v1/tasks/{id}", 200, 0.01)
    metrics.observe("GET", "/api/v1/tasks/{id}", 401, 0.01)
    metrics.observe("PROPFIND", "unmatched", 404, 0.01)
    lines = metrics.render()

    assert 'jarvis_http_requests_total{method="GET",route="/api/v1/tasks/{id}",status="200"} 2' in lines
    assert 'jarvis_http_requests_total{method="GET",route="/api/v1/tasks/{id}",status="401"} 1' in lines
    assert 'jarvis_http_requests_total{method="OTHER",route="unmatched",status="404"} 1' in lines


# -- request recording with a fake clock ---------------------------------------


class FakeClock:
    """Stands in for the ``time`` module inside the middleware: perf_counter returns what the test sets."""

    def __init__(self) -> None:
        self.now = 100.0

    def perf_counter(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Route:
    """Stands in for a FastAPI route. The middleware only reads ``path``."""

    def __init__(self, path: str) -> None:
        self.path = path


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(middleware_module, "time", fake)
    return fake


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> RequestMetrics:
    fresh = RequestMetrics()
    monkeypatch.setattr(middleware_module, "REQUEST_METRICS", fresh)
    return fresh


def _middleware(inner: Callable[..., Awaitable[None]], tmp_path: Path) -> ApiSecurityMiddleware:
    settings = Settings(jarvis_env="test", data_dir=tmp_path)
    return ApiSecurityMiddleware(inner, settings=settings, rate_limiter=RateLimiter())


def _scope(method: str, path: str) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "client": ("203.0.113.5", 4000),
    }


async def _drive(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _responder(clock: FakeClock, template: str, elapsed: float) -> Callable[..., Awaitable[None]]:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope["route"] = _Route(template)
        clock.advance(elapsed)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return app


async def test_middleware_records_template_status_and_time_to_response_start(
    clock: FakeClock, registry: RequestMetrics, tmp_path: Path
) -> None:
    app = _middleware(_responder(clock, TASK_TEMPLATE, 0.02), tmp_path)
    await _drive(app, _scope("GET", f"/api/v1/tasks/{TASK_ID}?x=1"))

    lines = registry.render()
    assert 'jarvis_http_requests_total{method="GET",route="/api/v1/tasks/{id}",status="200"} 1' in lines
    buckets = _bucket_values(lines)
    assert buckets["0.01"] == 0
    assert buckets["0.025"] == 1


async def test_exception_before_response_start_is_recorded_as_500(
    clock: FakeClock, registry: RequestMetrics, tmp_path: Path
) -> None:
    async def failing(scope: dict[str, Any], receive: Any, send: Any) -> None:
        clock.advance(0.5)
        raise RuntimeError("boom")

    app = _middleware(failing, tmp_path)
    with pytest.raises(RuntimeError):
        await _drive(app, _scope("GET", "/api/v1/tasks"))

    assert 'jarvis_http_requests_total{method="GET",route="unmatched",status="500"} 1' in registry.render()


async def test_only_api_requests_other_than_the_metrics_endpoint_are_recorded(
    clock: FakeClock, registry: RequestMetrics, tmp_path: Path
) -> None:
    app = _middleware(_responder(clock, "/api/v1/metrics", 0.01), tmp_path)
    await _drive(app, _scope("GET", "/api/v1/metrics"))
    static = _middleware(_responder(clock, "/index.html", 0.01), tmp_path)
    await _drive(static, _scope("GET", "/index.html"))

    assert not any(line.startswith("jarvis_http_requests_total{") for line in registry.render())
