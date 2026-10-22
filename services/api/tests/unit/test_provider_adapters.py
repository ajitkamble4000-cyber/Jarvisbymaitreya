"""Adapter tests for Gemini and Hugging Face. Every request goes through httpx.MockTransport; nothing reaches the network.

These tests verify request shape, response parsing, error mapping, streaming and timeouts against
scripted responses. They do not prove that the live services accept these requests.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from app.providers.base import (
    ChatMessage,
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    StreamEvent,
    TokenUsage,
    ToolDefinition,
)
from app.providers.gemini import GeminiProvider
from app.providers.huggingface import HuggingFaceProvider

GEMINI_KEY = "AIzaSy-unit-test-secret-9f3c"
HF_TOKEN = "hf_unit_test_secret_token_7d2a"
GEMINI_BASE = "https://gemini.example.test"
HF_BASE = "https://hf.example.test"
WHOAMI_URL = "https://whoami.example.test/api/whoami-v2"

Handler = Callable[[httpx.Request], httpx.Response | Any]

WEATHER_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gemini(handler: Handler, *, max_retries: int = 0) -> GeminiProvider:
    return GeminiProvider(
        api_key=GEMINI_KEY,
        base_url=GEMINI_BASE,
        http=_client(handler),
        timeout_seconds=5.0,
        max_retries=max_retries,
    )


def _huggingface(handler: Handler, *, max_retries: int = 0) -> HuggingFaceProvider:
    return HuggingFaceProvider(
        api_key=HF_TOKEN,
        base_url=HF_BASE,
        http=_client(handler),
        whoami_url=WHOAMI_URL,
        timeout_seconds=5.0,
        max_retries=max_retries,
    )


def _gemini_request(**overrides: Any) -> GenerationRequest:
    values: dict[str, Any] = {
        "model": "gemini-2.5-flash",
        "messages": [
            ChatMessage(role="system", content="Be brief."),
            ChatMessage(role="user", content="Hi"),
            ChatMessage(role="assistant", content="Hello"),
            ChatMessage(role="user", content="Weather in Oslo?"),
        ],
        "temperature": 0.2,
        "max_output_tokens": 256,
        "tools": [ToolDefinition(name="get_weather", description="Current weather", parameters=WEATHER_SCHEMA)],
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _hf_request(**overrides: Any) -> GenerationRequest:
    values: dict[str, Any] = {
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "messages": [
            ChatMessage(role="system", content="Be brief."),
            ChatMessage(role="user", content="Weather in Oslo?"),
        ],
        "temperature": 0.5,
        "max_output_tokens": 64,
        "tools": [ToolDefinition(name="get_weather", description="Current weather", parameters=WEATHER_SCHEMA)],
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _sse(*events: dict[str, Any] | str, done: bool = False) -> bytes:
    """Builds an SSE body. Strings are written verbatim (comments, raw lines); dicts become JSON data lines."""
    lines: list[str] = []
    for event in events:
        if isinstance(event, str):
            lines.append(event)
        else:
            lines.append("data: " + json.dumps(event))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


class _BrokenStream(httpx.AsyncByteStream):
    """Yields one chunk, then fails the way a stalled connection does."""

    def __init__(self, first_chunk: bytes, error: Exception) -> None:
        self._first_chunk = first_chunk
        self._error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._first_chunk
        raise self._error


async def _collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


# ---------------------------------------------------------------------------
# Gemini: generation
# ---------------------------------------------------------------------------


async def test_gemini_request_shape_and_response_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "Sunny "},
                                {"text": "today."},
                                {"functionCall": {"name": "get_weather", "args": {"city": "Oslo"}}},
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5, "totalTokenCount": 17},
            },
        )

    result = await _gemini(handler).generate_response(_gemini_request())

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{GEMINI_BASE}/v1beta/models/gemini-2.5-flash:generateContent"
    assert request.headers["x-goog-api-key"] == GEMINI_KEY
    assert GEMINI_KEY not in str(request.url)

    body = json.loads(request.content)
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Hi"}]},
        {"role": "model", "parts": [{"text": "Hello"}]},
        {"role": "user", "parts": [{"text": "Weather in Oslo?"}]},
    ]
    assert body["systemInstruction"] == {"parts": [{"text": "Be brief."}]}
    assert body["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 256}
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "get_weather"
    assert declaration["parameters"]["properties"] == {"city": {"type": "string"}}
    # Gemini rejects these JSON Schema keywords, so they are removed before sending.
    assert "$schema" not in declaration["parameters"]
    assert "additionalProperties" not in declaration["parameters"]

    assert result.model == "gemini-2.5-flash"
    assert result.text == "Sunny today."
    assert result.finish_reason == "STOP"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Oslo"}
    assert str(uuid.UUID(call.id)) == call.id
    assert result.usage == TokenUsage(input_tokens=12, output_tokens=5, total_tokens=17, estimated=False)


async def test_gemini_omits_optional_fields_when_not_requested() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})

    request = _gemini_request(messages=[ChatMessage(role="user", content="Hi")], temperature=None, max_output_tokens=None, tools=[])
    result = await _gemini(handler).generate_response(request)

    body = json.loads(seen[0].content)
    assert "systemInstruction" not in body
    assert "generationConfig" not in body
    assert "tools" not in body
    assert result.usage is None
    assert result.text == "ok"


async def test_gemini_maps_tool_role_to_user_and_skips_thought_parts() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "internal reasoning", "thought": True},
                                {"text": "Answer."},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    request = _gemini_request(messages=[ChatMessage(role="user", content="Hi"), ChatMessage(role="tool", content="result")], tools=[])
    result = await _gemini(handler).generate_response(request)

    body = json.loads(seen[0].content)
    assert [item["role"] for item in body["contents"]] == ["user", "user"]
    assert result.text == "Answer."


async def test_gemini_rejects_invalid_model_names_without_calling_the_service() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request(model="../other/path"))
    assert info.value.code is ProviderErrorCode.INVALID_REQUEST
    assert calls == 0


async def test_gemini_strips_models_prefix_from_the_model_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]})

    await _gemini(handler).generate_response(_gemini_request(model="models/gemini-2.5-pro"))
    assert str(seen[0].url).endswith("/v1beta/models/gemini-2.5-pro:generateContent")


async def test_gemini_request_without_user_messages_is_invalid() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request(messages=[ChatMessage(role="system", content="Only system")]))
    assert info.value.code is ProviderErrorCode.INVALID_REQUEST
    assert calls == 0


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (400, ProviderErrorCode.INVALID_REQUEST, False),
        (401, ProviderErrorCode.AUTH_FAILED, False),
        (403, ProviderErrorCode.AUTH_FAILED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (500, ProviderErrorCode.UNAVAILABLE, True),
        (503, ProviderErrorCode.UNAVAILABLE, True),
    ],
)
async def test_gemini_http_errors_are_mapped_and_never_echo_the_key(
    status: int,
    code: ProviderErrorCode,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # The upstream body repeats the key on purpose. The adapter must not pass it on.
        return httpx.Response(status, json={"error": {"message": f"problem with key {GEMINI_KEY}"}})

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request())
    assert info.value.code is code
    assert info.value.retryable is retryable
    assert info.value.status_code == status
    assert GEMINI_KEY not in info.value.message


async def test_gemini_400_with_block_reason_is_content_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"promptFeedback": {"blockReason": "SAFETY"}})

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request())
    assert info.value.code is ProviderErrorCode.CONTENT_BLOCKED
    assert info.value.retryable is False


async def test_gemini_safety_finish_without_text_is_content_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]})

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request())
    assert info.value.code is ProviderErrorCode.CONTENT_BLOCKED


async def test_gemini_prompt_block_without_candidates_is_content_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}})

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request())
    assert info.value.code is ProviderErrorCode.CONTENT_BLOCKED


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ReadTimeout("read timed out"), ProviderErrorCode.TIMEOUT),
        (httpx.ConnectTimeout("connect timed out"), ProviderErrorCode.TIMEOUT),
        (httpx.ConnectError("connection refused"), ProviderErrorCode.NETWORK_ERROR),
    ],
)
async def test_gemini_timeouts_and_connection_failures_are_retryable(exc: Exception, code: ProviderErrorCode) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ProviderError) as info:
        await _gemini(handler).generate_response(_gemini_request())
    assert info.value.code is code
    assert info.value.retryable is True


async def test_gemini_retries_rate_limits_when_configured() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "after retry"}]}}]})

    result = await _gemini(handler, max_retries=1).generate_response(_gemini_request())
    assert attempts == 2
    assert result.text == "after retry"


async def test_gemini_does_not_retry_auth_failures() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={})

    with pytest.raises(ProviderError):
        await _gemini(handler, max_retries=3).generate_response(_gemini_request())
    assert attempts == 1


# ---------------------------------------------------------------------------
# Gemini: streaming
# ---------------------------------------------------------------------------


async def test_gemini_stream_parses_text_tool_calls_usage_and_done() -> None:
    body = _sse(
        ": keep-alive comment",
        {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]},
        {
            "candidates": [
                {
                    "content": {"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Oslo"}}}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
        },
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = await _collect(_gemini(handler).stream_response(_gemini_request()))

    request = seen[0]
    assert request.url.params["alt"] == "sse"
    assert request.url.path.endswith("gemini-2.5-flash:streamGenerateContent")
    assert request.headers["x-goog-api-key"] == GEMINI_KEY
    assert GEMINI_KEY not in str(request.url)

    assert [event.type for event in events] == ["text_delta", "text_delta", "tool_call", "usage", "done"]
    assert "".join(event.text or "" for event in events if event.type == "text_delta") == "Hello"
    tool_event = events[2]
    assert tool_event.tool_call is not None
    assert tool_event.tool_call.name == "get_weather"
    assert tool_event.tool_call.arguments == {"city": "Oslo"}
    assert events[3].usage == TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5, estimated=False)
    assert events[4].finish_reason == "STOP"


async def test_gemini_stream_safety_without_text_raises_content_blocked() -> None:
    body = _sse({"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    received: list[StreamEvent] = []
    with pytest.raises(ProviderError) as info:
        async for event in _gemini(handler).stream_response(_gemini_request()):
            received.append(event)
    assert info.value.code is ProviderErrorCode.CONTENT_BLOCKED
    assert received == []


async def test_gemini_stream_unreadable_event_is_an_unknown_error() -> None:
    body = _sse("data: {not json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with pytest.raises(ProviderError) as info:
        await _collect(_gemini(handler).stream_response(_gemini_request()))
    assert info.value.code is ProviderErrorCode.UNKNOWN
    assert info.value.retryable is False


async def test_gemini_stream_open_is_retried_before_any_event() -> None:
    attempts = 0
    body = _sse({"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, content=body)

    events = await _collect(_gemini(handler, max_retries=1).stream_response(_gemini_request()))
    assert attempts == 2
    assert events[0].text == "ok"


async def test_gemini_stream_open_failure_without_retries_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(ProviderError) as info:
        await _collect(_gemini(handler, max_retries=0).stream_response(_gemini_request()))
    assert info.value.code is ProviderErrorCode.RATE_LIMITED
    assert info.value.retryable is True


async def test_gemini_stream_timeout_after_first_event_is_not_retried() -> None:
    attempts = 0
    first = _sse({"candidates": [{"content": {"parts": [{"text": "Par"}]}}]})

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=_BrokenStream(first, httpx.ReadTimeout("stalled")))

    received: list[StreamEvent] = []
    with pytest.raises(ProviderError) as info:
        async for event in _gemini(handler, max_retries=3).stream_response(_gemini_request()):
            received.append(event)
    assert [event.text for event in received] == ["Par"]
    assert info.value.code is ProviderErrorCode.TIMEOUT
    assert attempts == 1


async def test_gemini_health_check_uses_free_model_list_and_reports_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"models": []})

    result = await _gemini(handler).health_check()
    assert result.ok is True
    assert result.error_code is None
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v1beta/models"
    assert seen[0].headers["x-goog-api-key"] == GEMINI_KEY
    assert GEMINI_KEY not in str(seen[0].url)
    assert GEMINI_KEY not in result.message


async def test_gemini_health_check_failure_is_reported_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": f"key {GEMINI_KEY} is not allowed"}})

    result = await _gemini(handler).health_check()
    assert result.ok is False
    assert result.error_code == ProviderErrorCode.AUTH_FAILED.value
    assert GEMINI_KEY not in result.message


async def test_gemini_health_check_timeout_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    result = await _gemini(handler).health_check()
    assert result.ok is False
    assert result.error_code == ProviderErrorCode.TIMEOUT.value


async def test_gemini_lists_generation_models_across_pages() -> None:
    pages: dict[str | None, dict[str, Any]] = {
        None: {
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "displayName": "Gemini 2.5 Flash",
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            ],
            "nextPageToken": "page-2",
        },
        "page-2": {
            "models": [
                {
                    "name": "models/gemini-2.5-pro",
                    "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
                    "supportsTools": True,
                }
            ]
        },
    }
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("pageToken")])

    models = await _gemini(handler).list_models()

    assert [model.id for model in models] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert models[0].display_name == "Gemini 2.5 Flash"
    assert models[0].supports_tools is None  # not stated by the response
    assert models[0].supports_streaming is False
    assert models[1].supports_tools is True
    assert models[1].supports_streaming is True
    assert seen[0].url.params["pageSize"] == "1000"
    assert seen[1].url.params["pageToken"] == "page-2"
    assert all(GEMINI_KEY not in str(request.url) for request in seen)


async def test_gemini_stops_when_a_page_token_repeats() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"models": [], "nextPageToken": "same"})

    assert await _gemini(handler).list_models() == []
    assert calls == 2


# ---------------------------------------------------------------------------
# Hugging Face: chat completions
# ---------------------------------------------------------------------------


async def test_huggingface_chat_request_shape_and_response_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Checking.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": '{"city": "Oslo"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            },
        )

    result = await _huggingface(handler).generate_response(_hf_request())

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{HF_BASE}/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {HF_TOKEN}"
    assert HF_TOKEN not in str(request.url)

    body = json.loads(request.content)
    assert body["model"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Weather in Oslo?"},
    ]
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.5
    assert body["stream"] is False
    assert body["tools"] == [
        {"type": "function", "function": {"name": "get_weather", "description": "Current weather", "parameters": WEATHER_SCHEMA}}
    ]

    assert result.text == "Checking."
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"city": "Oslo"}
    assert result.usage == TokenUsage(input_tokens=20, output_tokens=6, total_tokens=26, estimated=False)


async def test_huggingface_bad_tool_arguments_fall_back_to_empty_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"id": "call_bad", "function": {"name": "get_weather", "arguments": "{not json"}},
                                {"id": "call_list", "function": {"name": "get_weather", "arguments": "[1, 2]"}},
                                {"function": {"name": "get_weather", "arguments": '{"city": "Bergen"}'}},
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    result = await _huggingface(handler).generate_response(_hf_request())
    assert result.text == ""
    assert [call.arguments for call in result.tool_calls] == [{}, {}, {"city": "Bergen"}]
    assert result.tool_calls[2].id.startswith("call_")
    uuid.UUID(hex=result.tool_calls[2].id.removeprefix("call_"))  # raises if the generated id is malformed


async def test_huggingface_omits_tools_and_limits_when_not_requested() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    result = await _huggingface(handler).generate_response(
        _hf_request(temperature=None, max_output_tokens=None, tools=[]),
    )
    body = json.loads(seen[0].content)
    assert "tools" not in body
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert result.usage is None
    assert result.text == "ok"


async def test_huggingface_rejects_empty_message_lists_without_calling_the_service() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(ProviderError) as info:
        await _huggingface(handler).generate_response(_hf_request(messages=[]))
    assert info.value.code is ProviderErrorCode.INVALID_REQUEST
    assert calls == 0


async def test_huggingface_response_without_choices_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ProviderError) as info:
        await _huggingface(handler).generate_response(_hf_request())
    assert info.value.code is ProviderErrorCode.UNKNOWN


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (400, ProviderErrorCode.INVALID_REQUEST, False),
        (401, ProviderErrorCode.AUTH_FAILED, False),
        (403, ProviderErrorCode.AUTH_FAILED, False),
        (404, ProviderErrorCode.MODEL_NOT_FOUND, False),
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (500, ProviderErrorCode.UNAVAILABLE, True),
        (503, ProviderErrorCode.UNAVAILABLE, True),
    ],
)
async def test_huggingface_http_errors_are_mapped_and_never_echo_the_token(
    status: int,
    code: ProviderErrorCode,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": f"token {HF_TOKEN} rejected"})

    with pytest.raises(ProviderError) as info:
        await _huggingface(handler).generate_response(_hf_request())
    assert info.value.code is code
    assert info.value.retryable is retryable
    assert HF_TOKEN not in info.value.message


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ReadTimeout("read timed out"), ProviderErrorCode.TIMEOUT),
        (httpx.ConnectError("connection refused"), ProviderErrorCode.NETWORK_ERROR),
    ],
)
async def test_huggingface_timeouts_and_connection_failures_are_retryable(exc: Exception, code: ProviderErrorCode) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ProviderError) as info:
        await _huggingface(handler).generate_response(_hf_request())
    assert info.value.code is code
    assert info.value.retryable is True


# ---------------------------------------------------------------------------
# Hugging Face: streaming
# ---------------------------------------------------------------------------


def _hf_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


async def test_huggingface_stream_emits_text_assembled_tool_call_usage_and_done() -> None:
    body = _sse(
        ": ping",
        _hf_chunk({"role": "assistant", "content": "Hel"}),
        _hf_chunk({"content": "lo"}),
        _hf_chunk({"tool_calls": [{"index": 0, "id": "call_9", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":'}}]}),
        _hf_chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "Oslo"}'}}]}, finish_reason="tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}},
        done=True,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = await _collect(_huggingface(handler).stream_response(_hf_request()))

    sent = json.loads(seen[0].content)
    assert sent["stream"] is True
    assert seen[0].headers["authorization"] == f"Bearer {HF_TOKEN}"
    assert [event.type for event in events] == ["text_delta", "text_delta", "tool_call", "usage", "done"]
    assert events[0].text == "Hel"
    assert events[1].text == "lo"
    call = events[2].tool_call
    assert call is not None
    assert call.id == "call_9"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Oslo"}
    assert events[3].usage == TokenUsage(input_tokens=7, output_tokens=4, total_tokens=11, estimated=False)
    assert events[4].finish_reason == "tool_calls"


async def test_huggingface_stream_bad_tool_arguments_fall_back_to_empty_object() -> None:
    body = _sse(
        _hf_chunk({"tool_calls": [{"index": 0, "id": "call_x", "function": {"name": "get_weather", "arguments": "{oops"}}]}, finish_reason="tool_calls"),
        done=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    events = await _collect(_huggingface(handler).stream_response(_hf_request()))
    call_events = [event for event in events if event.type == "tool_call"]
    assert len(call_events) == 1
    assert call_events[0].tool_call is not None
    assert call_events[0].tool_call.arguments == {}


async def test_huggingface_stream_without_done_or_finish_reason_is_an_error() -> None:
    body = _sse(_hf_chunk({"content": "partial"}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    received: list[StreamEvent] = []
    with pytest.raises(ProviderError) as info:
        async for event in _huggingface(handler).stream_response(_hf_request()):
            received.append(event)
    assert [event.text for event in received] == ["partial"]
    assert info.value.code is ProviderErrorCode.UNAVAILABLE
    assert info.value.retryable is False


async def test_huggingface_stream_error_payload_is_reported() -> None:
    body = _sse({"error": {"message": "model overloaded"}}, done=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with pytest.raises(ProviderError) as info:
        await _collect(_huggingface(handler).stream_response(_hf_request()))
    assert info.value.code is ProviderErrorCode.UNAVAILABLE


async def test_huggingface_stream_open_failure_respects_retry_budget() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={})

    with pytest.raises(ProviderError) as info:
        await _collect(_huggingface(handler, max_retries=1).stream_response(_hf_request()))
    assert info.value.code is ProviderErrorCode.UNAVAILABLE
    assert attempts == 2


async def test_huggingface_stream_timeout_after_text_is_not_retried() -> None:
    attempts = 0
    first = _sse(_hf_chunk({"content": "Par"}))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=_BrokenStream(first, httpx.ReadTimeout("stalled")))

    received: list[StreamEvent] = []
    with pytest.raises(ProviderError) as info:
        async for event in _huggingface(handler, max_retries=3).stream_response(_hf_request()):
            received.append(event)
    assert [event.text for event in received] == ["Par"]
    assert info.value.code is ProviderErrorCode.TIMEOUT
    assert attempts == 1


# ---------------------------------------------------------------------------
# Hugging Face: health check, models and capabilities
# ---------------------------------------------------------------------------


async def test_huggingface_health_check_uses_whoami_and_reports_account_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"type": "user", "name": "ada-lovelace"})

    result = await _huggingface(handler).health_check()

    assert result.ok is True
    assert result.account_label == "ada-lovelace"
    assert len(seen) == 1
    assert str(seen[0].url) == WHOAMI_URL
    assert seen[0].method == "GET"
    assert seen[0].headers["authorization"] == f"Bearer {HF_TOKEN}"
    assert HF_TOKEN not in result.message


async def test_huggingface_health_check_without_name_has_no_account_label() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "org"})

    result = await _huggingface(handler).health_check()
    assert result.ok is True
    assert result.account_label is None


async def test_huggingface_health_check_failure_is_reported_without_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"Invalid token {HF_TOKEN}"})

    result = await _huggingface(handler).health_check()
    assert result.ok is False
    assert result.error_code == ProviderErrorCode.AUTH_FAILED.value
    assert HF_TOKEN not in result.message


async def test_huggingface_lists_models_from_the_openai_compatible_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "meta-llama/Llama-3.1-8B-Instruct"}, {"id": 7}, {"id": "Qwen/Qwen2.5-7B"}]},
        )

    models = await _huggingface(handler).list_models()
    assert [model.id for model in models] == ["meta-llama/Llama-3.1-8B-Instruct", "Qwen/Qwen2.5-7B"]
    assert all(model.supports_tools is None for model in models)


def test_capabilities_match_the_contract() -> None:
    gemini = _gemini(lambda request: httpx.Response(200)).get_capabilities()
    assert gemini.streaming is True
    assert gemini.tool_calling is True
    assert gemini.usage_reporting is True
    assert gemini.model_listing is True

    huggingface = _huggingface(lambda request: httpx.Response(200)).get_capabilities()
    assert huggingface.streaming is True
    assert huggingface.tool_calling is None  # depends on the model
    assert huggingface.usage_reporting is True
    assert huggingface.model_listing is True


def test_adapter_repr_never_exposes_the_key() -> None:
    gemini = _gemini(lambda request: httpx.Response(200))
    huggingface = _huggingface(lambda request: httpx.Response(200))
    assert GEMINI_KEY not in repr(gemini)
    assert HF_TOKEN not in repr(huggingface)


def test_provider_identities() -> None:
    assert GeminiProvider.provider_id == "gemini"
    assert GeminiProvider.display_name == "Google Gemini"
    assert HuggingFaceProvider.provider_id == "huggingface"
    assert HuggingFaceProvider.display_name == "Hugging Face Inference"
