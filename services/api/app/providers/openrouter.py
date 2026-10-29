"""OpenRouter adapter: OpenAI-compatible chat completions through OpenRouter.

The API key travels only in the ``Authorization`` header and the request metadata headers.
It is never placed in a URL, logged, or included in an error message.
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
    ToolDefinition,
)
from app.providers.retry import elapsed_ms, int_or_none, response_json, run_with_retries, status_error, stream_with_retries, transport_error

log = logging.getLogger("jarvis.providers")

_ROLE_MAP: dict[str, str] = {"system": "system", "user": "user", "assistant": "assistant", "tool": "tool"}
_MAX_MODEL_ID_CHARS = 200


class OpenRouterProvider(AIProvider):
    provider_id: ClassVar[str] = "openrouter"
    display_name: ClassVar[str] = "OpenRouter"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        http: httpx.AsyncClient,
        site_url: str = "https://localhost",
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, http=http)
        self._site_url = site_url
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    @property
    def _chat_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    @property
    def _models_url(self) -> str:
        return f"{self._base_url}/models"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            multimodal_input=False,
            usage_reporting=True,
            model_listing=True,
            system_instructions=True,
        )

    async def generate_response(self, request: GenerationRequest) -> GenerationResult:
        model = _model_id(request.model)
        body = _chat_body(request, model=model, stream=False)
        started = time.perf_counter()

        async def attempt() -> dict[str, Any]:
            return await self._send("POST", self._chat_url, json_body=body, timeout=request.timeout_seconds)

        payload = await run_with_retries(attempt, max_retries=self._max_retries)
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
        started = time.perf_counter()
        try:
            await run_with_retries(
                partial(self._send, "GET", self._models_url, params={"limit": "1"}, timeout=self._timeout_seconds),
                max_retries=self._max_retries,
            )
        except ProviderError as exc:
            return HealthCheckResult(
                ok=False,
                latency_ms=elapsed_ms(started),
                message=exc.message,
                error_code=exc.code.value,
            )
        return HealthCheckResult(
            ok=True,
            latency_ms=elapsed_ms(started),
            message="OpenRouter accepted the API key.",
        )

    async def list_models(self) -> list[ModelInfo]:
        payload = await run_with_retries(
            partial(self._send, "GET", self._models_url, params={"limit": "200"}, timeout=self._timeout_seconds),
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
                models.append(ModelInfo(id=model_id, display_name=item.get("name") or model_id))
        return models

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": self._site_url,
            "X-Title": "JARVIS",
        }

    async def _send(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise transport_error(self.display_name, exc) from exc
        if response.status_code >= 400:
            raise status_error(self.display_name, response.status_code)
        return response_json(response)

    async def _stream_events(
        self,
        body: dict[str, Any],
        timeout: float,
    ) -> AsyncGenerator[StreamEvent, None]:
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
                    choices = chunk.get("choices")
                    if not isinstance(choices, list):
                        continue
                    for choice in choices:
                        if not isinstance(choice, dict):
                            continue
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            text = delta.get("content")
                            if isinstance(text, str) and text:
                                yield StreamEvent(type="text_delta", text=text)
                            tool_calls = delta.get("tool_calls")
                            if isinstance(tool_calls, list):
                                for fragment in tool_calls:
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
                "OpenRouter ended the stream before the response completed.",
            )
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
        raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "The OpenRouter model name is not valid.")
    return model


def _chat_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": _ROLE_MAP.get(message.role, "user"), "content": message.content} for message in messages]


def _chat_body(request: GenerationRequest, *, model: str, stream: bool) -> dict[str, Any]:
    if not request.messages:
        raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "Send at least one message to OpenRouter.")
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
        raise ProviderError(ProviderErrorCode.UNKNOWN, "OpenRouter sent a stream event that could not be read.") from exc
    if not isinstance(value, dict):
        raise ProviderError(ProviderErrorCode.UNKNOWN, "OpenRouter sent a stream event that could not be read.")
    return value


def _parse_completion(payload: dict[str, Any]) -> tuple[str, list[ToolCall], str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(ProviderErrorCode.UNKNOWN, "OpenRouter returned no completion choices.")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderError(ProviderErrorCode.UNKNOWN, "OpenRouter returned an unreadable completion choice.")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderError(ProviderErrorCode.UNKNOWN, "OpenRouter returned no completion message.")
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    raw_calls = message.get("tool_calls")
    tool_calls: list[ToolCall] = []
    if isinstance(raw_calls, list):
        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            fn = raw.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            args = fn.get("arguments")
            if not isinstance(name, str) or not name:
                continue
            if isinstance(args, str):
                try:
                    parsed = json.loads(args)
                except json.JSONDecodeError:
                    parsed = {}
                arguments = parsed if isinstance(parsed, dict) else {}
            elif isinstance(args, dict):
                arguments = args
            else:
                arguments = {}
            call_id = raw.get("id")
            tool_calls.append(
                ToolCall(
                    id=call_id if isinstance(call_id, str) and call_id else f"call_{uuid.uuid4().hex}",
                    name=name,
                    arguments=arguments,
                )
            )
    return text, tool_calls, choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None


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


def _merge_tool_fragment(fragments: dict[int, dict[str, Any]], raw: object) -> None:
    if not isinstance(raw, dict):
        return
    index = raw.get("index")
    if not isinstance(index, int):
        return
    current = fragments.setdefault(index, {"id": None, "name": None, "arguments": ""})
    function = raw.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str):
            current["name"] = name
        arg_text = function.get("arguments")
        if isinstance(arg_text, str):
            current["arguments"] += arg_text
    call_id = raw.get("id")
    if isinstance(call_id, str):
        current["id"] = call_id


def _finish_tool_fragment(fragment: dict[str, Any]) -> ToolCall | None:
    name = fragment.get("name")
    if not isinstance(name, str) or not name:
        return None
    arguments_text = fragment.get("arguments")
    if not isinstance(arguments_text, str):
        arguments_text = ""
    try:
        parsed = json.loads(arguments_text) if arguments_text else {}
    except json.JSONDecodeError:
        parsed = {}
    arguments = parsed if isinstance(parsed, dict) else {}
    call_id = fragment.get("id")
    return ToolCall(
        id=call_id if isinstance(call_id, str) and call_id else f"call_{uuid.uuid4().hex}",
        name=name,
        arguments=arguments,
    )
