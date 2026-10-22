"""Pure ASGI middleware: request IDs, security headers, origin and CSRF checks, rate limiting, access logs.

These are deliberately written as raw ASGI middleware rather than ``BaseHTTPMiddleware``:
the latter buffers response bodies in some Starlette versions, which would break the
Server-Sent Events stream used by the web app.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.deps import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.core.config import Settings
from app.security.ratelimit import RateLimiter
from app.security.tokens import constant_time_equals
from app.services.metrics import REQUEST_METRICS, route_label

log = logging.getLogger("jarvis.access")

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
_API_PREFIX = "/api/v1"
_AUTH_PATHS = ("/api/v1/auth/login", "/api/v1/auth/setup")
# Scrapes are not recorded, so the metrics endpoint does not count itself.
_METRICS_PATH = "/api/v1/metrics"

Headers = list[tuple[bytes, bytes]]


def _get_header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _cookies(scope: Scope) -> dict[str, str]:
    raw = _get_header(scope, b"cookie")
    return cookie_parser(raw) if raw else {}


def _route_template(scope: Scope) -> str | None:
    """Path template of the route that served this request, or None when no route matched.

    Read when the response starts, after routing. FastAPI 0.146 (pinned) keeps the full template, such as
    ``/api/v1/tasks/{task_id}``, in the effective route context of a nested router. ``scope["route"]`` holds
    only the route's own path (``/tasks/{task_id}``), so it is the fallback. A request refused before routing
    (origin, CSRF, rate limit) has neither and is labelled unmatched.
    """
    fastapi_scope = scope.get("fastapi")
    context = fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, dict) else None
    route = context if context is not None else scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else None


async def _send_json(send: Send, status: int, code: str, message: str, request_id: str) -> None:
    body = json.dumps(
        {"error": {"code": code, "message": message, "details": {}}, "request_id": request_id}
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-request-id", request_id.encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class ApiSecurityMiddleware:
    """Applies request IDs, security headers, origin/CSRF checks, rate limiting and access logging."""

    def __init__(self, app: ASGIApp, *, settings: Settings, rate_limiter: RateLimiter) -> None:
        self.app = app
        self.settings = settings
        self.rate_limiter = rate_limiter
        self._allowed_origins = set(settings.allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = _get_header(scope, b"x-request-id") or ""
        request_id = incoming_id if _REQUEST_ID_RE.match(incoming_id) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        method: str = scope["method"]
        path: str = scope["path"]
        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 500}
        recorded = False

        async def send_wrapper(message: Message) -> None:
            nonlocal recorded
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers: Headers = list(message.get("headers", []))
                names = {key.lower() for key, _ in headers}
                headers.extend(self._security_headers(request_id, path, has_cache_control=b"cache-control" in names))
                message["headers"] = headers
                if not recorded:
                    recorded = True
                    self._record(scope, message["status"], started)
            await send(message)

        if path.startswith(_API_PREFIX):
            rejection = self._check_request(scope, method, path)
            if rejection is not None:
                status, code, text = rejection
                await _send_json(send_wrapper, status, code, text, request_id)
                self._log(method, path, status_holder["status"], started, request_id)
                return

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # An exception before any response start is answered by the outer error handler, which bypasses
            # send_wrapper. Those requests are recorded here with the 500 that the client receives.
            if not recorded:
                self._record(scope, status_holder["status"], started)
            self._log(method, path, status_holder["status"], started, request_id)

    def _record(self, scope: Scope, status: int, started: float) -> None:
        """Counts one /api/v1 request. Called when its response starts, so streams are timed to their headers."""
        path: str = scope["path"]
        if not path.startswith(_API_PREFIX) or path == _METRICS_PATH:
            return
        elapsed = time.perf_counter() - started
        REQUEST_METRICS.observe(scope["method"], route_label(path, _route_template(scope)), status, elapsed)

    def _check_request(self, scope: Scope, method: str, path: str) -> tuple[int, str, str] | None:
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        if method in _UNSAFE_METHODS:
            origin = _get_header(scope, b"origin")
            if origin is not None and origin not in self._allowed_origins:
                return 403, "origin_not_allowed", "This origin is not allowed to make changes."

            cookies = _cookies(scope)
            if SESSION_COOKIE in cookies:
                csrf_cookie = cookies.get(CSRF_COOKIE, "")
                csrf_header = _get_header(scope, CSRF_HEADER.encode()) or ""
                if not csrf_cookie or not constant_time_equals(csrf_cookie, csrf_header):
                    return 403, "csrf_failed", "Security token missing or invalid. Reload the page and try again."

        limit = self.settings.login_rate_limit_per_minute if path in _AUTH_PATHS else self.settings.api_rate_limit_per_minute
        if not self.rate_limiter.hit(f"api:{client_ip}:{path if path in _AUTH_PATHS else 'all'}", limit):
            return 429, "rate_limited", "Too many requests. Slow down and try again shortly."
        return None

    def _security_headers(self, request_id: str, path: str, *, has_cache_control: bool) -> Headers:
        headers: Headers = [
            (b"x-request-id", request_id.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
        ]
        if path.startswith(_API_PREFIX):
            # API responses carry personal data: never store them, and they are never rendered as a page.
            # A route that sets its own Cache-Control (the event stream) keeps it.
            if not has_cache_control:
                headers.append((b"cache-control", b"no-store"))
            headers.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
        if self.settings.cookie_secure:
            headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        return headers

    def _log(self, method: str, path: str, status: int, started: float, request_id: str) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "%s %s %s",
            method,
            path,
            status,
            extra={"request_id": request_id, "status": status, "duration_ms": duration_ms, "method": method},
        )
