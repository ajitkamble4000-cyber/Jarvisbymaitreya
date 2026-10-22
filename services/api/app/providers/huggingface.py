"""Hugging Face Inference adapter: OpenAI-compatible chat completions on the Hugging Face router.

The token travels only in the ``Authorization: Bearer`` header. It is never placed in a URL,
logged, or included in an error message.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from functools import partial
from typing import Any, ClassVar

import httpx

from app.providers.base import (
    AIProvider,
    ChatMessage,
    GenerationRequest,
    GenerationResult,
    HealthCheckResult,
    ModelInfo,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
    StreamEvent,
    TokenUsage,
    ToolCall,
)
from app.providers.retry import (
    elapsed_ms,
    int_or_none,
    response_json,
    run_with_retries,
    status_error,
    stream_with_retries,
    transport_error,
)

log = logging.getLogger("jarvis.providers")

_ROLE_MAP: dict[str, str] = {"system": "system", "user": "user", "assistant": "assistant", "tool": "user"}
_DEFAULT_WHOAMI_URL = "https://huggingface.co/api/whoami-v2"
_MAX_MODEL_ID_CHARS = 200


class HuggingFaceProvider(AIProvider):
    provider_id: ClassVar[str] = "huggingface"
    display_name: ClassVar[str] = "Hugging Face Inference"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        http: httpx.AsyncClient,
        whoami_url: str = _DEFAULT_WHOAMI_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, http=http)
        self._whoami_url = whoami_url
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    @property
    def _chat_url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    @property
    def _models_url(self) -> str:
        return f"{self._base_url}/v1/models"

    def get_capabilities(self) -> ProviderCapabilities:
        # Tool calling depends on the selected model, so it stays unknown until a model is tested.
        return ProviderCapabilities(
            streaming=True,
            tool_calling=None,
            multimodal_input=False,
            usage_reporting=True,
            model_listing=True,
        )

    async def generate_response(self, request: GenerationRequest) -> GenerationResult:
        model = _model_id(request.model)
        body = _chat_body(request, model=model, stream=False)
        started = time.perf_counter()
        payload = await run_with_retries(
            partial(self._send, "POST", self._chat_url, json_body=body, timeout=request.timeout_seconds),
            max_retries=self._max_retries,
        )
        text, tool_calls, finish_reason = _parse_completion(payload)
        return GenerationResult(
            model=model,
            text=text,
            tool_calls=tool_calls,
            usage=_usage(payload.get("usage")),
            finish_reason=finish_reason,
            latency_ms=elapsed_ms(started),
        )

    async def stream_response(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        model = _model_id(request.model)
        body = _chat_body(request, model=model, stream=True)
        events = stream_with_retries(
            partial(self._stream_events, body, request.timeout_seconds),
            max_retries=self._max_retries,
        )
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()

    async def health_check(self) -> HealthCheckResult:
        """Checks the token with the free whoami endpoint. Generates no content."""
        started = time.perf_counter()
        try:
            payload = await run_with_retries(
                partial(self._send, "GET", self._whoami_url, timeout=self._timeout_seconds),
                max_retries=self._max_retries,
            )
        except ProviderError as exc:
            return HealthCheckResult(
                ok=False,
                latency_ms=elapsed_ms(started),
                message=exc.message,
                error_code=exc.code.value,
            )
        name = payload.get("name")
        account_label = name.strip() if isinstance(name, str) and name.strip() else None
        return HealthCheckResult(
            ok=True,
            latency_ms=elapsed_ms(started),
            message="Hugging Face accepted the access token.",
            account_label=account_label,
        )

    async def list_models(self) -> list[ModelInfo]:
        payload = await run_with_retries(
            partial(self._send, "GET", self._models_url, timeout=self._timeout_seconds),
            max_retries=self._max_retries,
        )
        items = payload.get("data")
        if not isinstance(items, list):
            return []
        models: list[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and 0 < len(model_id) <= _MAX_MODEL_ID_CHARS:
                models.append(ModelInfo(id=model_id, display_name=model_id))
        return models

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _send(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise transport_error(self.display_name, exc) from exc
        if response.status_code >= 400:
            raise status_error(self.display_name, response.status_code)
        return response_json(response)

    async def _stream_events(self, body: dict[str, Any], timeout: float) -> AsyncGenerator[StreamEvent, None]:
        fragments: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: TokenUsage | None = None
        done_seen = False
        try:
            async with self._http.stream(
                "POST",
                self._chat_url,
                headers=self._headers(),
                json=body,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise status_error(self.display_name, response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if not raw:
                        continue
                    if raw == "[DONE]":
                        done_seen = True
                        break
                    chunk = _decode_chunk(raw)
                    if chunk.get("error"):
                        raise ProviderError(
                            ProviderErrorCode.UNAVAILABLE,
                            "Hugging Face reported an error while streaming the response.",
                        )
                    choices = chunk.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                        choice = choices[0]
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                yield StreamEvent(type="text_delta", text=content)
                            fragment_list = delta.get("tool_calls")
                            if isinstance(fragment_list, list):
                                for fragment in fragment_list:
                                    _merge_tool_fragment(fragments, fragment)
                        reason = choice.get("finish_reason")
                        if isinstance(reason, str) and reason:
                            finish_reason = reason
                    chunk_usage = _usage(chunk.get("usage"))
                    if chunk_usage is not None:
                        usage = chunk_usage
        except httpx.HTTPError as exc:
            raise transport_error(self.display_name, exc) from exc

        if not done_seen and finish_reason is None:
            raise ProviderError(
                ProviderErrorCode.UNAVAILABLE,
                "Hugging Face ended the stream before the response completed.",
            )
        # Tool calls are complete only once the stream ends, so they are emitted here, in index order.
        for index in sorted(fragments):
            call = _finish_tool_fragment(fragments[index])
            if call is not None:
                yield StreamEvent(type="tool_call", tool_call=call)
        if usage is not None:
            yield StreamEvent(type="usage", usage=usage)
        yield StreamEvent(type="done", finish_reason=finish_reason)


def _model_id(value: str) -> str:
    model = value.strip()
    if not model or len(model) > _MAX_MODEL_ID_CHARS:
        raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "The Hugging Face model name is not valid.")
    return model


def _chat_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": _ROLE_MAP[message.role], "content": message.content} for message in messages]


def _chat_body(request: GenerationRequest, *, model: str, stream: bool) -> dict[str, Any]:
    if not request.messages:
        raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "Send at least one message to Hugging Face.")
    body: dict[str, Any] = {
        "model": model,
        "messages": _chat_messages(request.messages),
        "stream": stream,
    }
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    return body


def _decode_chunk(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Hugging Face sent a stream event that could not be read.") from exc
    if not isinstance(value, dict):
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Hugging Face sent a stream event that could not be read.")
    return value


def _parse_arguments(raw: object) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string. Anything unparseable falls back to an empty object."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Hugging Face tool call arguments were not valid JSON; using an empty argument object.")
        return {}
    return value if isinstance(value, dict) else {}


def _completed_tool_call(raw: dict[str, Any]) -> ToolCall | None:
    function = raw.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    call_id = raw.get("id")
    return ToolCall(
        id=call_id if isinstance(call_id, str) and call_id else f"call_{uuid.uuid4().hex}",
        name=name,
        arguments=_parse_arguments(function.get("arguments")),
    )


def _merge_tool_fragment(fragments: dict[int, dict[str, Any]], raw: object) -> None:
    """Streamed tool calls arrive as fragments keyed by index. Names and ids are set; argument text is appended."""
    if not isinstance(raw, dict):
        return
    index = raw.get("index")
    key = index if isinstance(index, int) and not isinstance(index, bool) else len(fragments)
    entry = fragments.setdefault(key, {"id": None, "name": "", "arguments": ""})
    call_id = raw.get("id")
    if isinstance(call_id, str) and call_id:
        entry["id"] = call_id
    function = raw.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            entry["name"] = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            entry["arguments"] += arguments


def _finish_tool_fragment(entry: dict[str, Any]) -> ToolCall | None:
    if not entry["name"]:
        return None
    call_id = entry["id"] or f"call_{uuid.uuid4().hex}"
    return ToolCall(id=call_id, name=entry["name"], arguments=_parse_arguments(entry["arguments"]))


def _parse_completion(payload: dict[str, Any]) -> tuple[str, list[ToolCall], str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Hugging Face returned no choices.")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    tool_calls: list[ToolCall] = []
    raw_calls = message.get("tool_calls")
    for raw in raw_calls if isinstance(raw_calls, list) else []:
        if isinstance(raw, dict):
            call = _completed_tool_call(raw)
            if call is not None:
                tool_calls.append(call)
    finish_reason = choice.get("finish_reason")
    return text, tool_calls, finish_reason if isinstance(finish_reason, str) else None


def _usage(raw: object) -> TokenUsage | None:
    if not isinstance(raw, dict):
        return None
    input_tokens = int_or_none(raw.get("prompt_tokens"))
    output_tokens = int_or_none(raw.get("completion_tokens"))
    total_tokens = int_or_none(raw.get("total_tokens"))
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated=False,
    )
