"""Google Gemini adapter: Generative Language API (v1beta).

The API key travels only in the ``x-goog-api-key`` header. It is never placed in a URL,
logged, or included in an error message.
"""

from __future__ import annotations

import json
import math
import re
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
from app.providers.retry import (
    elapsed_ms,
    int_or_none,
    response_json,
    run_with_retries,
    status_error,
    stream_with_retries,
    transport_error,
)

_ROLE_MAP: dict[str, str] = {"user": "user", "assistant": "model", "tool": "user"}
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,200}")
_PAGE_SIZE = 1000
_MAX_MODEL_PAGES = 50
# The Gemini Schema type accepts an OpenAPI subset. These JSON Schema keywords are rejected by the API.
_UNSUPPORTED_SCHEMA_KEYS = frozenset({"$schema", "additionalProperties"})
# Largest number of texts sent in one batchEmbedContents call. Callers split larger inputs.
EMBED_BATCH_LIMIT = 100


class GeminiProvider(AIProvider):
    provider_id: ClassVar[str] = "gemini"
    display_name: ClassVar[str] = "Google Gemini"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        http: httpx.AsyncClient,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, http=http)
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    @property
    def _models_url(self) -> str:
        return f"{self._base_url}/v1beta/models"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            multimodal_input=False,
            usage_reporting=True,
            model_listing=True,
            embeddings=True,
        )

    async def generate_response(self, request: GenerationRequest) -> GenerationResult:
        model = _model_id(request.model)
        body = _request_body(request)
        url = f"{self._models_url}/{model}:generateContent"
        started = time.perf_counter()

        async def attempt() -> dict[str, Any]:
            return await self._send("POST", url, json_body=body, timeout=request.timeout_seconds)

        payload = await run_with_retries(attempt, max_retries=self._max_retries)
        text, tool_calls, finish_reason = _parse_generation(payload)
        return GenerationResult(
            model=model,
            text=text,
            tool_calls=tool_calls,
            usage=_usage(payload.get("usageMetadata")),
            finish_reason=finish_reason,
            latency_ms=elapsed_ms(started),
        )

    async def stream_response(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        model = _model_id(request.model)
        body = _request_body(request)
        url = f"{self._models_url}/{model}:streamGenerateContent"
        events = stream_with_retries(
            partial(self._stream_events, url, body, request.timeout_seconds),
            max_retries=self._max_retries,
        )
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
    ) -> list[list[float]]:
        """Batch embedding call. Returns one vector per text, in input order, each with ``dimensions`` values."""
        if not texts:
            return []
        if len(texts) > EMBED_BATCH_LIMIT:
            raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "Too many texts for one embedding request.")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "Embedding input must be non-empty text.")
        model_id = _model_id(model)
        url = f"{self._models_url}/{model_id}:batchEmbedContents"
        body: dict[str, Any] = {
            "requests": [
                {
                    "model": f"models/{model_id}",
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": dimensions,
                }
                for text in texts
            ]
        }

        async def attempt() -> dict[str, Any]:
            return await self._send("POST", url, json_body=body, timeout=timeout_seconds)

        payload = await run_with_retries(attempt, max_retries=self._max_retries)
        return _embedding_vectors(payload, expected_count=len(texts), dimensions=dimensions)

    async def health_check(self) -> HealthCheckResult:
        started = time.perf_counter()
        try:
            await run_with_retries(
                partial(self._send, "GET", self._models_url, params={"pageSize": "1"}, timeout=self._timeout_seconds),
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
            message="Google Gemini accepted the API key.",
        )

    async def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        seen_tokens: set[str] = set()
        page_token: str | None = None
        for _ in range(_MAX_MODEL_PAGES):
            payload = await run_with_retries(
                partial(self._fetch_model_page, page_token),
                max_retries=self._max_retries,
            )
            models.extend(_model_infos(payload))
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
                return models
            seen_tokens.add(next_token)
            page_token = next_token
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini returned more model pages than expected.")

    async def _fetch_model_page(self, page_token: str | None) -> dict[str, Any]:
        params = {"pageSize": str(_PAGE_SIZE)}
        if page_token:
            params["pageToken"] = page_token
        return await self._send("GET", self._models_url, params=params, timeout=self._timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}

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
            raise _http_error(self.display_name, response)
        return response_json(response)

    async def _stream_events(
        self,
        url: str,
        body: dict[str, Any],
        timeout: float,
    ) -> AsyncGenerator[StreamEvent, None]:
        text_seen = False
        calls_seen = False
        finish_reason: str | None = None
        usage: TokenUsage | None = None
        try:
            async with self._http.stream(
                "POST",
                url,
                headers=self._headers(),
                json=body,
                params={"alt": "sse"},
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise _http_error(self.display_name, response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if not raw:
                        continue
                    chunk = _decode_event(raw)
                    candidates = chunk.get("candidates")
                    has_candidates = isinstance(candidates, list) and bool(candidates)
                    if not has_candidates and _block_reason(chunk):
                        raise ProviderError(ProviderErrorCode.CONTENT_BLOCKED, "Google Gemini blocked this request.")
                    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
                        candidate = candidates[0]
                        text, calls = _parts(candidate)
                        if text:
                            text_seen = True
                            yield StreamEvent(type="text_delta", text=text)
                        for call in calls:
                            calls_seen = True
                            yield StreamEvent(type="tool_call", tool_call=call)
                        finish_reason = _str_or_none(candidate.get("finishReason")) or finish_reason
                    chunk_usage = _usage(chunk.get("usageMetadata"))
                    if chunk_usage is not None:
                        usage = chunk_usage
        except httpx.HTTPError as exc:
            raise transport_error(self.display_name, exc) from exc

        if finish_reason == "SAFETY" and not text_seen and not calls_seen:
            raise ProviderError(ProviderErrorCode.CONTENT_BLOCKED, "Google Gemini blocked the response for safety reasons.")
        if usage is not None:
            yield StreamEvent(type="usage", usage=usage)
        yield StreamEvent(type="done", finish_reason=finish_reason)


def _http_error(provider_name: str, response: httpx.Response) -> ProviderError:
    body = response_json(response)
    return status_error(provider_name, response.status_code, block_reason=_block_reason(body))


def _model_id(value: str) -> str:
    model = value.strip().removeprefix("models/")
    if not _MODEL_ID_RE.fullmatch(model):
        raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "The Gemini model name is not valid.")
    return model


def _role_contents(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [
        {"role": _ROLE_MAP[message.role], "parts": [{"text": message.content}]}
        for message in messages
        if message.role != "system" and message.content
    ]


def _request_body(request: GenerationRequest) -> dict[str, Any]:
    system_texts = [message.content for message in request.messages if message.role == "system" and message.content.strip()]
    contents = _role_contents(request.messages)
    if not contents:
        raise ProviderError(ProviderErrorCode.INVALID_REQUEST, "Send at least one user message to Gemini.")

    body: dict[str, Any] = {"contents": contents}
    if system_texts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
    generation: dict[str, Any] = {}
    if request.temperature is not None:
        generation["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        generation["maxOutputTokens"] = request.max_output_tokens
    if generation:
        body["generationConfig"] = generation
    if request.tools:
        body["tools"] = [{"functionDeclarations": [_declaration(tool) for tool in request.tools]}]
    return body


def _declaration(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": _gemini_schema(tool.parameters),
    }


def _gemini_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _gemini_schema(item) for key, item in value.items() if key not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(value, list):
        return [_gemini_schema(item) for item in value]
    return value


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _block_reason(payload: dict[str, Any]) -> str | None:
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict):
        return _str_or_none(feedback.get("blockReason"))
    return None


def _decode_event(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini sent a stream event that could not be read.") from exc
    if not isinstance(value, dict):
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini sent a stream event that could not be read.")
    return value


def _parts(candidate: dict[str, Any]) -> tuple[str, list[ToolCall]]:
    """Joins text parts and converts function calls. Internal "thought" parts are skipped."""
    content = candidate.get("content")
    raw_parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(raw_parts, list):
        return "", []
    texts: list[str] = []
    calls: list[ToolCall] = []
    for part in raw_parts:
        if not isinstance(part, dict) or part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
        function_call = part.get("functionCall")
        if isinstance(function_call, dict) and isinstance(function_call.get("name"), str):
            args = function_call.get("args")
            calls.append(
                ToolCall(
                    id=str(uuid.uuid4()),
                    name=function_call["name"],
                    arguments=args if isinstance(args, dict) else {},
                )
            )
    return "".join(texts), calls


def _parse_generation(payload: dict[str, Any]) -> tuple[str, list[ToolCall], str | None]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        if _block_reason(payload):
            raise ProviderError(ProviderErrorCode.CONTENT_BLOCKED, "Google Gemini blocked this request.")
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini returned no response candidates.")
    candidate = candidates[0]
    text, calls = _parts(candidate)
    finish_reason = _str_or_none(candidate.get("finishReason"))
    if finish_reason == "SAFETY" and not text and not calls:
        raise ProviderError(ProviderErrorCode.CONTENT_BLOCKED, "Google Gemini blocked the response for safety reasons.")
    return text, calls, finish_reason


def _embedding_vectors(payload: dict[str, Any], *, expected_count: int, dimensions: int) -> list[list[float]]:
    """Checks the batch response shape: one finite vector of the requested size per input text."""
    items = payload.get("embeddings")
    if not isinstance(items, list) or len(items) != expected_count:
        raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini returned an unexpected embedding response.")
    vectors: list[list[float]] = []
    for item in items:
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list) or len(values) != dimensions:
            raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini returned an embedding of the wrong size.")
        vector: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ProviderError(ProviderErrorCode.UNKNOWN, "Google Gemini returned an unreadable embedding.")
            vector.append(float(value))
        vectors.append(vector)
    return vectors


def _usage(metadata: object) -> TokenUsage | None:
    if not isinstance(metadata, dict):
        return None
    input_tokens = int_or_none(metadata.get("promptTokenCount"))
    output_tokens = int_or_none(metadata.get("candidatesTokenCount"))
    total_tokens = int_or_none(metadata.get("totalTokenCount"))
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated=False,
    )


def _model_infos(payload: dict[str, Any]) -> list[ModelInfo]:
    items = payload.get("models")
    if not isinstance(items, list):
        return []
    models: list[ModelInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods")
        if not isinstance(methods, list) or "generateContent" not in methods:
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        model_id = name.removeprefix("models/")
        if not _MODEL_ID_RE.fullmatch(model_id):
            continue
        supports_tools = item.get("supportsTools")
        models.append(
            ModelInfo(
                id=model_id,
                display_name=_str_or_none(item.get("displayName")),
                supports_tools=supports_tools if isinstance(supports_tools, bool) else None,
                supports_streaming="streamGenerateContent" in methods,
            )
        )
    return models
