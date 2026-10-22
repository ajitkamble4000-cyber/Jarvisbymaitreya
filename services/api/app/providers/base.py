"""Provider-independent AI interface.

Every hosted model provider (Gemini, Hugging Face, future adapters) implements
``AIProvider``. Callers only use these types, never a vendor SDK. Capabilities are
declared explicitly: a method that a provider does not support raises
``ProviderError(UNSUPPORTED)`` instead of pretending to work.

Credentials are passed to the constructor and are never logged, serialised or
included in ``repr``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field


class ProviderErrorCode(StrEnum):
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    UNAVAILABLE = "unavailable"
    NETWORK_ERROR = "network_error"
    NOT_CONFIGURED = "not_configured"
    UNSUPPORTED = "unsupported"
    CONTENT_BLOCKED = "content_blocked"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """A provider call failed. ``message`` is safe to show to the user; it never contains credentials."""

    def __init__(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderCapabilities(_Model):
    streaming: bool = False
    # None means "not known for this provider/model until tested".
    tool_calling: bool | None = None
    multimodal_input: bool = False
    usage_reporting: bool = False
    model_listing: bool = False
    system_instructions: bool = True
    # True when ``embed`` works for this provider. Semantic memory search needs it.
    embeddings: bool = False


class ChatMessage(_Model):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ToolDefinition(_Model):
    name: str
    description: str
    # JSON Schema object describing the arguments.
    parameters: dict[str, Any]


class ToolCall(_Model):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class TokenUsage(_Model):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    # True when the provider did not report usage and the value was estimated locally.
    estimated: bool = False


class GenerationRequest(_Model):
    model: str
    messages: list[ChatMessage]
    tools: list[ToolDefinition] = Field(default_factory=list)
    temperature: float | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float = 30.0


class GenerationResult(_Model):
    model: str
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    latency_ms: int = 0


class StreamEvent(_Model):
    type: Literal["text_delta", "tool_call", "usage", "done"]
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: TokenUsage | None = None
    finish_reason: str | None = None


class ModelInfo(_Model):
    id: str
    display_name: str | None = None
    supports_tools: bool | None = None
    supports_streaming: bool | None = None
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])


class HealthCheckResult(_Model):
    ok: bool
    latency_ms: int
    message: str
    error_code: str | None = None
    account_label: str | None = None


class UsageEstimate(_Model):
    input_tokens: int
    output_tokens: int | None = None
    method: Literal["chars_div_4"] = "chars_div_4"
    estimated: bool = True


class AIProvider(ABC):
    """Base class for all AI provider adapters."""

    provider_id: ClassVar[str]
    display_name: ClassVar[str]

    def __init__(self, *, api_key: str, base_url: str, http: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http

    def __repr__(self) -> str:  # never expose the credential
        return f"<{type(self).__name__} base_url={self._base_url!r}>"

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """Capabilities that are true for this provider. Per-model details come from ``list_models``."""

    @abstractmethod
    async def generate_response(self, request: GenerationRequest) -> GenerationResult:
        """Single non-streaming completion. Honours ``request.timeout_seconds``."""

    async def stream_response(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        """Streaming completion. Providers without streaming keep this default."""
        raise ProviderError(
            ProviderErrorCode.UNSUPPORTED,
            f"{self.display_name} does not support streaming responses.",
        )
        yield  # pragma: no cover  - makes this an async generator

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
    ) -> list[list[float]]:
        """One embedding vector per input text, in input order. Providers without embeddings keep this default."""
        raise ProviderError(
            ProviderErrorCode.UNSUPPORTED,
            f"{self.display_name} does not support embeddings.",
        )

    @abstractmethod
    async def health_check(self) -> HealthCheckResult:
        """Verifies the credential and connectivity without generating content."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Models currently available to this credential."""

    def estimate_usage(self, request: GenerationRequest) -> UsageEstimate:
        """Local approximation (about four characters per token). Used only when the provider reports nothing."""
        characters = sum(len(message.content) for message in request.messages)
        characters += sum(len(tool.name) + len(tool.description) for tool in request.tools)
        return UsageEstimate(input_tokens=max(1, characters // 4))
