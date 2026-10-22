"""Retry and timeout policy for provider calls, plus HTTP classification shared by the adapters.

Only a ``ProviderError`` with ``retryable=True`` is retried. Delays use full jitter: each wait is
drawn uniformly from ``[0, min(max_delay, base_delay * 2 ** attempt)]``, where ``attempt`` is 0 for
the first retry. A stream is retried only while it has not yielded anything. Once an event has
reached the caller, the error propagates unchanged so nothing is replayed or duplicated.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

import httpx

from app.providers.base import ProviderError, ProviderErrorCode

log = logging.getLogger("jarvis.providers")

T = TypeVar("T")
Sleep = Callable[[float], Awaitable[None]]

_MAX_BACKOFF_EXPONENT = 30


def _validate_policy(max_retries: int, base_delay: float, max_delay: float) -> None:
    if max_retries < 0:
        raise ValueError("max_retries must be zero or greater.")
    if base_delay < 0 or max_delay < 0:
        raise ValueError("Retry delays must be zero or greater.")


def backoff_delay(attempt: int, *, base_delay: float, max_delay: float) -> float:
    """Full-jitter delay in seconds for retry number ``attempt`` (0 for the first retry)."""
    exponent = min(attempt, _MAX_BACKOFF_EXPONENT)
    ceiling = min(max_delay, base_delay * (2**exponent))
    return random.uniform(0.0, ceiling)


def _log_retry(exc: ProviderError, attempt: int, delay: float) -> None:
    log.info(
        "Retrying provider call after a transient error.",
        extra={"code": exc.code.value, "attempt": attempt + 1, "delay_seconds": round(delay, 2)},
    )


async def run_with_retries(
    op: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Sleep = asyncio.sleep,
) -> T:
    """Runs ``op`` once, then retries retryable ``ProviderError`` values up to ``max_retries`` times."""
    _validate_policy(max_retries, base_delay, max_delay)
    attempt = 0
    while True:
        try:
            return await op()
        except ProviderError as exc:
            if not exc.retryable or attempt >= max_retries:
                raise
            delay = backoff_delay(attempt, base_delay=base_delay, max_delay=max_delay)
            _log_retry(exc, attempt, delay)
            await sleep(delay)
            attempt += 1


async def stream_with_retries(
    open_stream: Callable[[], AsyncGenerator[T, None]],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Sleep = asyncio.sleep,
) -> AsyncGenerator[T, None]:
    """Streams events from ``open_stream``, retrying only failures that happen before the first event.

    ``open_stream`` is called again for every attempt. After the first event has been yielded, any
    error is raised to the caller without a retry.
    """
    _validate_policy(max_retries, base_delay, max_delay)
    attempt = 0
    while True:
        started = False
        events = open_stream()
        try:
            async for event in events:
                started = True
                yield event
            return
        except ProviderError as exc:
            if started or not exc.retryable or attempt >= max_retries:
                raise
            delay = backoff_delay(attempt, base_delay=base_delay, max_delay=max_delay)
            _log_retry(exc, attempt, delay)
            await sleep(delay)
            attempt += 1
        finally:
            await events.aclose()


# ---------------------------------------------------------------------------
# HTTP classification shared by the adapters. Each mapping sets ``retryable``
# so the policy above and the callers agree on what is transient.
# ---------------------------------------------------------------------------


def elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def response_json(response: httpx.Response) -> dict[str, Any]:
    """Parses a JSON object body. Anything else (or an unreadable body) yields an empty dict."""
    try:
        data = response.json()
    except (ValueError, RuntimeError):
        return {}
    return data if isinstance(data, dict) else {}


def int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def transport_error(provider_name: str, exc: httpx.HTTPError) -> ProviderError:
    """Maps an httpx failure to a ProviderError. Timeouts and connection failures are retryable."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(ProviderErrorCode.TIMEOUT, f"{provider_name} did not respond in time.", retryable=True)
    if isinstance(exc, httpx.TransportError):
        return ProviderError(
            ProviderErrorCode.NETWORK_ERROR,
            f"Could not reach {provider_name}. Check the network connection.",
            retryable=True,
        )
    return ProviderError(ProviderErrorCode.UNKNOWN, f"The request to {provider_name} failed.")


def status_error(provider_name: str, status: int, *, block_reason: str | None = None) -> ProviderError:
    """Maps an HTTP error status to a ProviderError. Messages never include request or response secrets."""
    if status in (400, 422):
        if block_reason:
            return ProviderError(ProviderErrorCode.CONTENT_BLOCKED, f"{provider_name} blocked this request.", status_code=status)
        return ProviderError(
            ProviderErrorCode.INVALID_REQUEST,
            f"{provider_name} rejected the request (HTTP {status}).",
            status_code=status,
        )
    if status in (401, 403):
        return ProviderError(
            ProviderErrorCode.AUTH_FAILED,
            f"{provider_name} rejected the API key or its permissions.",
            status_code=status,
        )
    if status == 404:
        return ProviderError(
            ProviderErrorCode.MODEL_NOT_FOUND,
            f"The model or endpoint was not found at {provider_name} (HTTP 404). Refresh the model list.",
            status_code=status,
        )
    if status == 408:
        return ProviderError(
            ProviderErrorCode.TIMEOUT,
            f"{provider_name} timed out (HTTP 408).",
            retryable=True,
            status_code=status,
        )
    if status == 429:
        return ProviderError(
            ProviderErrorCode.RATE_LIMITED,
            f"{provider_name} rate limit reached. Try again shortly.",
            retryable=True,
            status_code=status,
        )
    if status >= 500:
        return ProviderError(
            ProviderErrorCode.UNAVAILABLE,
            f"{provider_name} is unavailable right now (HTTP {status}).",
            retryable=True,
            status_code=status,
        )
    return ProviderError(
        ProviderErrorCode.UNKNOWN,
        f"{provider_name} returned an unexpected status (HTTP {status}).",
        status_code=status,
    )
