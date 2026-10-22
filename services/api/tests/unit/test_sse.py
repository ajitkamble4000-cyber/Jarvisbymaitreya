"""Server-sent event framing and keep-alive behaviour. Pure asyncio, no database."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.services.sse import (
    KEEP_ALIVE_COMMENT,
    KEEP_ALIVE_SECONDS,
    ServerSentEvent,
    encode_stream,
    json_event,
    keep_alive,
    validate_event_name,
    with_keepalive,
)


def test_keep_alive_interval_is_fixed_at_fifteen_seconds() -> None:
    assert KEEP_ALIVE_SECONDS == 15.0


def test_event_frame_has_name_data_and_blank_line_terminator() -> None:
    frame = json_event("text_delta", {"message_id": "m1", "text": "Hi"}).encode().decode("utf-8")

    assert frame == 'event: text_delta\ndata: {"message_id":"m1","text":"Hi"}\n\n'


def test_json_payload_stays_on_one_data_line_even_with_newlines_in_text() -> None:
    frame = json_event("text_delta", {"text": "line one\nline two\r\nthree"}).encode().decode("utf-8")

    data_lines = [line for line in frame.split("\n") if line.startswith("data: ")]
    assert len(data_lines) == 1
    assert "\\n" in data_lines[0]


def test_raw_multi_line_data_gets_one_data_prefix_per_line() -> None:
    frame = ServerSentEvent(event="note", data="first\nsecond\r\nthird\rfourth").encode().decode("utf-8")

    assert frame == "event: note\ndata: first\ndata: second\ndata: third\ndata: fourth\n\n"


def test_empty_data_is_still_a_complete_frame() -> None:
    frame = ServerSentEvent(event="done", data="").encode().decode("utf-8")

    assert frame == "event: done\ndata: \n\n"


def test_comment_frame_for_keep_alive() -> None:
    frame = keep_alive().encode().decode("utf-8")

    assert frame == f": {KEEP_ALIVE_COMMENT}\n\n"


def test_multi_line_comment_gets_a_colon_per_line() -> None:
    assert ServerSentEvent(comment="a\nb").encode() == b": a\n: b\n\n"


@pytest.mark.parametrize("name", ["", "has space", "bad\nname", "bad\rname", "x:y", "1starts-with-digit", "a" * 65])
def test_invalid_event_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        validate_event_name(name)


@pytest.mark.parametrize("name", ["done", "text_delta", "assistant.start", "tool-proposed", "Error"])
def test_valid_event_names_are_accepted(name: str) -> None:
    assert validate_event_name(name) == name


def test_unicode_text_is_encoded_as_utf8_without_escaping() -> None:
    frame = json_event("text_delta", {"text": "café ✓"}).encode()

    assert "café ✓".encode() in frame


async def _source(items: list[ServerSentEvent], *, delay: float = 0.0) -> AsyncIterator[ServerSentEvent]:
    for item in items:
        if delay:
            await asyncio.sleep(delay)
        yield item


async def test_keep_alive_is_sent_while_the_source_is_quiet_and_items_keep_their_order() -> None:
    items = [json_event("text_delta", {"text": "a"}), json_event("text_delta", {"text": "b"})]

    collected = [event async for event in with_keepalive(_source(items, delay=0.05), interval=0.01)]

    comments = [event for event in collected if event.comment is not None]
    payloads = [event for event in collected if event.event is not None]
    assert comments, "a quiet source must produce keep-alive comments"
    assert all(event.comment == KEEP_ALIVE_COMMENT for event in comments)
    assert [event.data for event in payloads] == [items[0].data, items[1].data]


async def test_no_keep_alive_when_items_arrive_before_the_interval() -> None:
    items = [json_event("done", {})]

    collected = [event async for event in with_keepalive(_source(items), interval=5.0)]

    assert collected == items


async def test_source_is_closed_when_the_consumer_stops_early() -> None:
    closed = asyncio.Event()

    async def endless() -> AsyncIterator[ServerSentEvent]:
        try:
            while True:
                yield json_event("text_delta", {"text": "x"})
        finally:
            closed.set()

    stream = with_keepalive(endless(), interval=1.0)
    first = await stream.__anext__()
    await stream.aclose()

    assert first.event == "text_delta"
    assert closed.is_set()


async def test_encode_stream_turns_frames_into_bytes_and_closes_the_source() -> None:
    items = [json_event("done", {}), ServerSentEvent(comment="bye")]

    chunks = [chunk async for chunk in encode_stream(_source(items))]

    assert chunks == [b'event: done\ndata: {}\n\n', b": bye\n\n"]


def test_keep_alive_interval_must_be_positive() -> None:
    async def run() -> None:
        async for _ in with_keepalive(_source([]), interval=0):
            pass

    with pytest.raises(ValueError):
        asyncio.run(run())
