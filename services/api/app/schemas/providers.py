"""Request and response models for the AI providers API (see docs/api.md, "AI providers")."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_API_KEY_CHARS = 512
_MAX_RAW_KEY_CHARS = 4096  # guards the request size before the stripped length is checked


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderCapabilitiesOut(BaseModel):
    streaming: bool
    # Null means unknown until a model is tested (Hugging Face depends on the model).
    tool_calling: bool | None
    multimodal_input: bool
    usage_reporting: bool
    model_listing: bool


class ProviderLastErrorOut(BaseModel):
    code: str
    message: str


class ProviderOut(BaseModel):
    id: str
    display_name: str
    enabled: bool
    configured: bool
    credential_hint: str | None = None
    selected_model: str | None = None
    timeout_seconds: float
    max_retries: int
    status: Literal["not_configured", "untested", "connected", "error"]
    last_tested_at: datetime | None = None
    last_latency_ms: int | None = None
    last_error: ProviderLastErrorOut | None = None
    capabilities: ProviderCapabilitiesOut


class ProviderListOut(BaseModel):
    items: list[ProviderOut]


class ProviderUpdate(_Strict):
    enabled: bool | None = None
    # Sent as null, this clears the selected model. Omitted, it is left unchanged.
    selected_model: str | None = Field(default=None, max_length=200)
    timeout_seconds: float | None = Field(default=None, ge=1, le=120)
    max_retries: int | None = Field(default=None, ge=0, le=5)

    @field_validator("selected_model")
    @classmethod
    def _check_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or _CONTROL_CHARS.search(cleaned):
            raise ValueError("Enter a valid model name.")
        return cleaned


class CredentialIn(_Strict):
    api_key: str = Field(max_length=_MAX_RAW_KEY_CHARS)

    @field_validator("api_key")
    @classmethod
    def _check_key(cls, value: str) -> str:
        # Validation messages never include the submitted value.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Enter an API key.")
        if len(cleaned) > _MAX_API_KEY_CHARS:
            raise ValueError("The API key is too long.")
        if _CONTROL_CHARS.search(cleaned):
            raise ValueError("The API key contains control characters.")
        return cleaned


class ProviderTestOut(BaseModel):
    ok: bool
    latency_ms: int | None
    message: str
    error_code: str | None
    account_label: str | None
    provider: ProviderOut


class ModelOut(BaseModel):
    id: str
    display_name: str | None = None
    supports_tools: bool | None = None
    supports_streaming: bool | None = None
    input_modalities: list[str]


class ModelListOut(BaseModel):
    items: list[ModelOut]


class UsageTotalsOut(BaseModel):
    requests: int
    successes: int
    failures: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    tokens_estimated: bool


class UsageRowOut(BaseModel):
    """Aggregate for one provider, or one provider and model pair. Unmeasured values are null."""

    provider_id: str
    display_name: str | None = None
    model: str | None = None
    requests: int
    successes: int
    failures: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    tokens_estimated: bool
    avg_latency_ms: float | None


class UsageErrorOut(BaseModel):
    code: str
    count: int


class UsageOut(BaseModel):
    period_days: int
    generated_at: datetime
    totals: UsageTotalsOut
    avg_latency_ms: float | None
    by_provider: list[UsageRowOut]
    by_model: list[UsageRowOut]
    error_frequency: list[UsageErrorOut]
    estimated_cost_usd: None = None
    cost_source: Literal["unavailable"] = "unavailable"
    cost_note: str
