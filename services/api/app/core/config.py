"""Application configuration loaded from environment variables (and an optional .env file).

Every field maps to an environment variable with the same name in upper case,
for example ``database_url`` is read from ``DATABASE_URL``. Secrets never have
defaults that would be unsafe in production; see ``validate_for_environment``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__


class ConfigurationError(RuntimeError):
    """Raised when the configuration is unsafe or incomplete for the environment."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "JARVIS AI"
    app_version: str = __version__
    # Fails closed: an unset JARVIS_ENV means production rules (secure cookies, no API docs, required
    # encryption key). Local development sets JARVIS_ENV=development explicitly (see .env.example).
    jarvis_env: Literal["development", "test", "production"] = "production"
    log_level: str = "INFO"

    # Persistence -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    data_dir: Path = Path("./data")

    # Secrets ---------------------------------------------------------------
    # Fernet key (urlsafe base64, 32 bytes). Required in production. In development
    # and test a key is generated into DATA_DIR/secret.key when this is unset.
    # SecretStr keeps the value out of repr(), str() and model_dump(). Read it with get_secret_value().
    encryption_key: SecretStr | None = None

    # Web security ----------------------------------------------------------
    public_base_url: str = "http://localhost:5173"
    # JSON list, e.g. JARVIS_ALLOWED_ORIGINS='["http://localhost:5173"]'
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    cookie_secure: bool = False
    session_ttl_hours: int = 12
    login_rate_limit_per_minute: int = 10
    api_rate_limit_per_minute: int = 600
    max_message_chars: int = 8000

    # Devices ---------------------------------------------------------------
    device_heartbeat_timeout_seconds: int = 45
    device_pairing_code_ttl_seconds: int = 600
    # The agent refuses commands that live longer than its replay cache can cover (about 9.5 minutes).
    device_command_ttl_seconds: int = Field(default=60, ge=1, le=540)
    device_command_timeout_seconds: int = 120
    device_max_pairing_attempts: int = 5

    # Tasks -----------------------------------------------------------------
    task_approval_ttl_seconds: int = 600
    task_queue_ttl_seconds: int = 1800
    max_tool_rounds_per_message: int = 3

    # AI providers ----------------------------------------------------------
    provider_default_timeout_seconds: float = 30.0
    provider_default_max_retries: int = 2
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    huggingface_base_url: str = "https://router.huggingface.co"
    huggingface_whoami_url: str = "https://huggingface.co/api/whoami-v2"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://localhost"

    # Optional integrations (OAuth). A provider is offered only when both of its values are set.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: SecretStr | None = None
    notion_oauth_client_id: str | None = None
    notion_oauth_client_secret: SecretStr | None = None
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=1800)

    # Prometheus-style metrics at /api/v1/metrics. Unset means the endpoint answers 404.
    metrics_token: SecretStr | None = None

    # Plugins: in-repo modules under app/plugins, loaded at startup. Only install code you have reviewed.
    enabled_plugins: list[str] = Field(default_factory=lambda: ["clock", "briefing", "calculator"])

    # Semantic memory. Search uses embeddings only when a Gemini embedding model is configured.
    gemini_embedding_model: str | None = None
    # The memory vector column is fixed at 768 dimensions. Any other value would silently disable embeddings.
    # An int bound, not Literal[768], so that the environment string "768" is accepted.
    embedding_dimensions: int = Field(default=768, ge=768, le=768)

    # Background work -------------------------------------------------------
    run_background_workers: bool = True
    scheduler_poll_seconds: float = 5.0
    sse_heartbeat_seconds: float = 15.0

    @property
    def is_production(self) -> bool:
        return self.jarvis_env == "production"

    @model_validator(mode="after")
    def validate_for_environment(self) -> Settings:
        if self.is_production:
            if not self.encryption_key:
                raise ConfigurationError("ENCRYPTION_KEY is required when JARVIS_ENV=production.")
            if not self.cookie_secure:
                raise ConfigurationError("COOKIE_SECURE must be true when JARVIS_ENV=production.")
            if not self.public_base_url.startswith("https://"):
                raise ConfigurationError("PUBLIC_BASE_URL must use https in production.")
            if "*" in self.allowed_origins:
                raise ConfigurationError("Wildcard ALLOWED_ORIGINS are not permitted.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
