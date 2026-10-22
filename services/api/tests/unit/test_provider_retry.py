"""Retry policy: which errors are retried, how many attempts run, jitter bounds, and stream behaviour."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import pytest

from app.providers import retry
from app.providers.base import ProviderError, ProviderErrorCode, StreamEvent
from app.providers.retry import backoff_delay, run_with_retries, stream_with_retries


class _Recorder:
    """Injected sleep. Records the requested delays without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _transient(code: ProviderErrorCode = ProviderErrorCode.RATE_LIMITED) -> ProviderError:
    return ProviderError(code, "temporary failure", retryable=True)


def _permanent(code: ProviderErrorCode = ProviderErrorCode.AUTH_FAILED) -> ProviderError:
    return ProviderError(code, "permanent failure", retryable=False)


async def test_success_on_first_attempt_does_not_sleep() -> None:
    sleep = _Recorder()
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await run_with_retries(op, max_retries=3, sleep=sleep) == "ok"
    assert calls == 1
    assert sleep.delays == []


async def test_retries_transient_errors_until_success() -> None:
    sleep = _Recorder()
    outcomes: list[object] = [_transient(), _transient(ProviderErrorCode.UNAVAILABLE), "done"]

    async def op() -> object:
        outcome = outcomes.pop(0)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome

    assert await run_with_retries(op, max_retries=2, sleep=sleep) == "done"
    assert len(sleep.delays) == 2
    assert outcomes == []


@pytest.mark.parametrize(
    "code",
    [
        ProviderErrorCode.AUTH_FAILED,
        ProviderErrorCode.INVALID_REQUEST,
        ProviderErrorCode.MODEL_NOT_FOUND,
        ProviderErrorCode.CONTENT_BLOCKED,
        ProviderErrorCode.NOT_CONFIGURED,
    ],
)
async def test_non_retryable_errors_are_raised_immediately(code: ProviderErrorCode) -> None:
    sleep = _Recorder()
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise _permanent(code)

    with pytest.raises(ProviderError) as info:
        await run_with_retries(op, max_retries=5, sleep=sleep)
    assert info.value.code is code
    assert calls == 1
    assert sleep.delays == []


async def test_stops_after_max_retries_and_raises_the_last_error() -> None:
    sleep = _Recorder()
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise ProviderError(ProviderErrorCode.TIMEOUT, f"attempt {calls}", retryable=True)

    with pytest.raises(ProviderError, match="attempt 4"):
        await run_with_retries(op, max_retries=3, sleep=sleep)
    assert calls == 4  # the first attempt plus three retries
    assert len(sleep.delays) == 3


async def test_zero_retries_calls_once() -> None:
    sleep = _Recorder()
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise _transient()

    with pytest.raises(ProviderError):
        await run_with_retries(op, max_retries=0, sleep=sleep)
    assert calls == 1
    assert sleep.delays == []


async def test_non_provider_exceptions_are_not_retried() -> None:
    sleep = _Recorder()
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("bug, not a provider failure")

    with pytest.raises(RuntimeError):
        await run_with_retries(op, max_retries=3, sleep=sleep)
    assert calls == 1
    assert sleep.delays == []


async def test_jitter_stays_within_exponential_bounds() -> None:
    sleep = _Recorder()

    async def op() -> None:
        raise _transient()

    with pytest.raises(ProviderError):
        await run_with_retries(op, max_retries=6, base_delay=0.5, max_delay=8.0, sleep=sleep)

    ceilings = [min(8.0, 0.5 * (2**attempt)) for attempt in range(6)]
    assert ceilings == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert len(sleep.delays) == 6
    for delay, ceiling in zip(sleep.delays, ceilings, strict=True):
        assert 0.0 <= delay <= ceiling


async def test_full_jitter_uses_zero_to_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    bounds: list[tuple[float, float]] = []

    def fake_uniform(low: float, high: float) -> float:
        bounds.append((low, high))
        return high

    monkeypatch.setattr(retry.random, "uniform", fake_uniform)
    sleep = _Recorder()

    async def op() -> None:
        raise _transient()

    with pytest.raises(ProviderError):
        await run_with_retries(op, max_retries=4, base_delay=1.0, max_delay=3.0, sleep=sleep)

    assert bounds == [(0.0, 1.0), (0.0, 2.0), (0.0, 3.0), (0.0, 3.0)]
    assert sleep.delays == [1.0, 2.0, 3.0, 3.0]


def test_backoff_delay_respects_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry.random, "uniform", lambda low, high: high)
    assert backoff_delay(0, base_delay=0.5, max_delay=8.0) == 0.5
    assert backoff_delay(10, base_delay=0.5, max_delay=8.0) == 8.0
    # Very large attempt numbers must not overflow.
    assert backoff_delay(10_000, base_delay=0.5, max_delay=8.0) == 8.0


@pytest.mark.parametrize("max_retries", [-1, -5])
async def test_negative_retry_counts_are_rejected(max_retries: int) -> None:
    async def op() -> None:
        return None

    with pytest.raises(ValueError):
        await run_with_retries(op, max_retries=max_retries, sleep=_Recorder())


# ---------------------------------------------------------------------------
# Streams: retries are allowed only before the first event reaches the caller.
# ---------------------------------------------------------------------------

StreamPlan = list[object]


def _scripted_stream(plans: list[StreamPlan]) -> tuple[Callable[[], AsyncGenerator[str, None]], list[int]]:
    """Each call consumes one plan. A ProviderError entry raises at that point in the stream."""
    opened: list[int] = []

    def open_stream() -> AsyncGenerator[str, None]:
        opened.append(1)
        plan = plans.pop(0)

        async def events() -> AsyncGenerator[str, None]:
            for item in plan:
                if isinstance(item, ProviderError):
                    raise item
                yield str(item)

        return events()

    return open_stream, opened


async def _collect(stream: AsyncGenerator[str, None]) -> list[str]:
    return [item async for item in stream]


async def test_stream_retries_when_the_open_fails_before_any_event() -> None:
    open_stream, opened = _scripted_stream([[_transient()], ["a", "b"]])
    sleep = _Recorder()
    items = await _collect(stream_with_retries(open_stream, max_retries=1, sleep=sleep))
    assert items == ["a", "b"]
    assert len(opened) == 2
    assert len(sleep.delays) == 1


async def test_stream_never_retries_after_an_event_was_yielded() -> None:
    open_stream, opened = _scripted_stream([["partial", _transient()], ["never", "reached"]])
    sleep = _Recorder()
    received: list[str] = []
    with pytest.raises(ProviderError) as info:
        async for item in stream_with_retries(open_stream, max_retries=3, sleep=sleep):
            received.append(item)
    assert received == ["partial"]
    assert info.value.retryable is True
    assert len(opened) == 1
    assert sleep.delays == []


async def test_stream_stops_after_max_retries_on_open_failures() -> None:
    open_stream, opened = _scripted_stream(
        [[_transient()], [_transient()], [_transient()]],
    )
    sleep = _Recorder()
    with pytest.raises(ProviderError):
        await _collect(stream_with_retries(open_stream, max_retries=2, sleep=sleep))
    assert len(opened) == 3
    assert len(sleep.delays) == 2


async def test_stream_does_not_retry_permanent_open_failures() -> None:
    open_stream, opened = _scripted_stream([[_permanent()], ["unused"]])
    with pytest.raises(ProviderError) as info:
        await _collect(stream_with_retries(open_stream, max_retries=3, sleep=_Recorder()))
    assert info.value.code is ProviderErrorCode.AUTH_FAILED
    assert len(opened) == 1


async def test_stream_event_type_is_preserved() -> None:
    async def open_stream() -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(type="text_delta", text="hi")
        yield StreamEvent(type="done", finish_reason="STOP")

    events = [event async for event in stream_with_retries(lambda: open_stream(), max_retries=0)]
    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[0].text == "hi"

