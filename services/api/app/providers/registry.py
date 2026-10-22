"""Provider registry: catalog, per-user configuration, credential handling, connection tests and models.

Every call made through the registry writes a ``UsageRecord`` (operation ``test`` or ``list_models``).
Chat calls are recorded by the chat service through the same ``record_usage`` function.
Methods here add rows to the caller's session; the caller commits.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models.ai import ProviderConfig
from app.providers.base import (
    AIProvider,
    HealthCheckResult,
    ModelInfo,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCode,
)
from app.providers.gemini import GeminiProvider
from app.providers.huggingface import HuggingFaceProvider
from app.providers.retry import elapsed_ms
from app.security.crypto import SecretBox, SecretDecryptionError, mask_secret
from app.services.audit import record_audit
from app.services.events import EventBus
from app.services.usage import record_usage

log = logging.getLogger("jarvis.providers")

_CATALOG: tuple[type[AIProvider], ...] = (GeminiProvider, HuggingFaceProvider)
_MAX_STORED_MESSAGE_CHARS = 300
_REDACTED = "[redacted]"


@dataclass(frozen=True)
class ConnectionTest:
    """Outcome of a connection test. ``latency_ms`` is None when no call was made."""

    ok: bool
    latency_ms: int | None
    message: str
    error_code: str | None
    account_label: str | None


def _scrub(text: str, api_key: str) -> str:
    """Removes the credential from provider text before it is stored or returned."""
    cleaned = text.replace(api_key, _REDACTED) if api_key else text
    return cleaned[:_MAX_STORED_MESSAGE_CHARS]


class ProviderRegistry:
    def __init__(
        self,
        *,
        settings: Settings,
        secret_box: SecretBox,
        http: httpx.AsyncClient,
        session_factory: async_sessionmaker,
        events: EventBus,
    ) -> None:
        self._settings = settings
        self._secret_box = secret_box
        self._http = http
        self._session_factory = session_factory
        self._events = events

    # -- catalog ------------------------------------------------------------

    def catalog(self) -> list[type[AIProvider]]:
        return list(_CATALOG)

    def provider_class(self, provider_id: str) -> type[AIProvider]:
        for provider_cls in _CATALOG:
            if provider_cls.provider_id == provider_id:
                return provider_cls
        raise AppError("Unknown AI provider.", code="unknown_provider", status_code=404)

    def capabilities_for(self, provider_cls: type[AIProvider]) -> ProviderCapabilities:
        """Capabilities do not depend on the key. The throwaway instance is never used for a call."""
        return self._instantiate(provider_cls, api_key="", timeout_seconds=0.0, max_retries=0).get_capabilities()

    # -- configuration rows -----------------------------------------------

    async def ensure_configs(self, db: AsyncSession, user_id: uuid.UUID) -> dict[str, ProviderConfig]:
        """Creates a default (disabled, not configured) row for each catalog provider that lacks one."""
        existing = {row.provider_id for row in await self._load_all(db, user_id)}
        for provider_cls in _CATALOG:
            if provider_cls.provider_id not in existing:
                await self._insert_default(db, user_id, provider_cls.provider_id)
        return {row.provider_id: row for row in await self._load_all(db, user_id)}

    async def get_config(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> ProviderConfig:
        self.provider_class(provider_id)
        config = await self._load(db, user_id, provider_id)
        if config is None:
            await self._insert_default(db, user_id, provider_id)
            config = await self._load(db, user_id, provider_id)
        if config is None:
            raise AppError("The provider settings could not be loaded.", code="internal_error", status_code=500)
        return config

    async def set_credential(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        provider_id: str,
        api_key: str,
    ) -> ProviderConfig:
        config = await self.get_config(db, user_id, provider_id)
        config.credential_ciphertext = self._secret_box.encrypt(api_key)
        config.credential_hint = mask_secret(api_key)
        config.status = "untested"
        config.last_error_code = None
        config.last_error_message = None
        record_audit(
            db,
            action="provider.credential_set",
            user_id=user_id,
            target_type="provider",
            target_id=provider_id,
            data={"provider_id": provider_id},
        )
        return config

    async def clear_credential(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> ProviderConfig:
        config = await self.get_config(db, user_id, provider_id)
        config.credential_ciphertext = None
        config.credential_hint = None
        config.status = "not_configured"
        config.enabled = False
        config.last_error_code = None
        config.last_error_message = None
        record_audit(
            db,
            action="provider.credential_deleted",
            user_id=user_id,
            target_type="provider",
            target_id=provider_id,
            data={"provider_id": provider_id},
        )
        return config

    # -- calls ----------------------------------------------------------------

    async def build(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> tuple[AIProvider, ProviderConfig]:
        """Returns a ready adapter for the user's configured provider. Chat uses this entry point.

        Raises ``ProviderError(NOT_CONFIGURED)`` when the user has no usable credential.
        The caller sets ``GenerationRequest.timeout_seconds`` from ``config.timeout_seconds``.
        """
        provider_cls = self.provider_class(provider_id)
        config = await self._load(db, user_id, provider_id)
        if config is None or not config.credential_ciphertext:
            raise _not_configured(provider_cls)
        api_key = self._decrypt(provider_cls, config)
        return self._instantiate_for(provider_cls, api_key, config), config

    async def test_connection(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        provider_id: str,
    ) -> tuple[ConnectionTest, ProviderConfig]:
        """Runs the free health check and stores the result. No content is generated."""
        provider_cls = self.provider_class(provider_id)
        config = await self.get_config(db, user_id, provider_id)
        if not config.credential_ciphertext:
            return (
                ConnectionTest(
                    ok=False,
                    latency_ms=None,
                    message="No API key is saved for this provider.",
                    error_code=ProviderErrorCode.NOT_CONFIGURED.value,
                    account_label=None,
                ),
                config,
            )

        api_key = self._decrypt(provider_cls, config)
        provider = self._instantiate_for(provider_cls, api_key, config)
        started = time.perf_counter()
        try:
            health = await provider.health_check()
        except ProviderError as exc:
            # The adapter normally returns a result instead of raising. This covers anything that escapes.
            health = HealthCheckResult(
                ok=False,
                latency_ms=elapsed_ms(started),
                message=exc.message,
                error_code=exc.code.value,
            )

        ok = health.ok
        message = _scrub(health.message, api_key)
        error_code = None if ok else (health.error_code or ProviderErrorCode.UNKNOWN.value)
        account_label = _scrub(health.account_label, api_key) if ok and health.account_label else None

        config.last_tested_at = utc_now()
        config.last_latency_ms = health.latency_ms
        config.status = "connected" if ok else "error"
        config.last_error_code = error_code
        config.last_error_message = None if ok else message

        record_usage(
            db,
            user_id=user_id,
            provider_id=provider_id,
            model=config.selected_model or "",
            operation="test",
            success=ok,
            latency_ms=health.latency_ms,
            error_code=error_code,
        )
        record_audit(
            db,
            action="provider.tested",
            user_id=user_id,
            target_type="provider",
            target_id=provider_id,
            outcome="success" if ok else "failure",
            data={"provider_id": provider_id, "latency_ms": health.latency_ms, "reason": error_code},
        )
        log.info("Provider connection test finished.", extra={"provider_id": provider_id, "ok": ok})
        return (
            ConnectionTest(
                ok=ok,
                latency_ms=health.latency_ms,
                message=message,
                error_code=error_code,
                account_label=account_label,
            ),
            config,
        )

    async def list_models(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> list[ModelInfo]:
        """Lists models available to the user's credential. Raises ``ProviderError`` on failure."""
        provider_cls = self.provider_class(provider_id)
        config = await self._load(db, user_id, provider_id)
        if config is None or not config.credential_ciphertext:
            raise _not_configured(provider_cls)
        api_key = self._decrypt(provider_cls, config)
        provider = self._instantiate_for(provider_cls, api_key, config)

        started = time.perf_counter()
        try:
            models = await provider.list_models()
        except ProviderError as exc:
            record_usage(
                db,
                user_id=user_id,
                provider_id=provider_id,
                model=config.selected_model or "",
                operation="list_models",
                success=False,
                latency_ms=elapsed_ms(started),
                error_code=exc.code.value,
            )
            raise
        record_usage(
            db,
            user_id=user_id,
            provider_id=provider_id,
            model=config.selected_model or "",
            operation="list_models",
            success=True,
            latency_ms=elapsed_ms(started),
        )
        return models

    # -- internals ------------------------------------------------------------

    async def _load(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> ProviderConfig | None:
        return await db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.user_id == user_id,
                ProviderConfig.provider_id == provider_id,
            )
        )

    async def _load_all(self, db: AsyncSession, user_id: uuid.UUID) -> list[ProviderConfig]:
        rows = await db.scalars(select(ProviderConfig).where(ProviderConfig.user_id == user_id))
        return list(rows.all())

    async def _insert_default(self, db: AsyncSession, user_id: uuid.UUID, provider_id: str) -> None:
        # ON CONFLICT keeps two concurrent first requests from creating duplicate rows.
        now = utc_now()
        statement = (
            pg_insert(ProviderConfig.__table__)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                provider_id=provider_id,
                enabled=False,
                selected_model=None,
                timeout_seconds=self._settings.provider_default_timeout_seconds,
                max_retries=self._settings.provider_default_max_retries,
                status="not_configured",
                capabilities={},
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_provider_configs_user_provider")
        )
        await db.execute(statement)

    def _decrypt(self, provider_cls: type[AIProvider], config: ProviderConfig) -> str:
        if not config.credential_ciphertext:
            raise _not_configured(provider_cls)
        try:
            return self._secret_box.decrypt(config.credential_ciphertext)
        except SecretDecryptionError as exc:
            raise ProviderError(
                ProviderErrorCode.NOT_CONFIGURED,
                f"The saved {provider_cls.display_name} key cannot be read. Save the key again.",
            ) from exc

    def _instantiate_for(self, provider_cls: type[AIProvider], api_key: str, config: ProviderConfig) -> AIProvider:
        return self._instantiate(
            provider_cls,
            api_key=api_key,
            timeout_seconds=float(config.timeout_seconds),
            max_retries=int(config.max_retries),
        )

    def _instantiate(
        self,
        provider_cls: type[AIProvider],
        *,
        api_key: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> AIProvider:
        if provider_cls is GeminiProvider:
            return GeminiProvider(
                api_key=api_key,
                base_url=self._settings.gemini_base_url,
                http=self._http,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
        if provider_cls is HuggingFaceProvider:
            return HuggingFaceProvider(
                api_key=api_key,
                base_url=self._settings.huggingface_base_url,
                http=self._http,
                whoami_url=self._settings.huggingface_whoami_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
        raise AppError("Unknown AI provider.", code="unknown_provider", status_code=404)


def _not_configured(provider_cls: type[AIProvider]) -> ProviderError:
    return ProviderError(
        ProviderErrorCode.NOT_CONFIGURED,
        f"No API key is saved for {provider_cls.display_name}. Add one in AI Models.",
    )
