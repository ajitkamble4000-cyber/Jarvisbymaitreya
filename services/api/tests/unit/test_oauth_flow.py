"""OAuth building blocks without a database: PKCE (RFC 7636), encrypted state, URLs, redirects and credentials."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.integrations.oauth.flow import (
    account_label_from_body,
    begin_connect,
    build_authorization_url,
    code_challenge,
    issue_state,
    new_code_verifier,
    open_state,
)
from app.integrations.oauth.providers import (
    GITHUB,
    GOOGLE_CALENDAR,
    credentials,
    get_provider,
    is_configured,
    parse_scopes,
    redirect_uri,
)
from app.integrations.oauth.tokens import (
    Credential,
    IntegrationError,
    credential_from_token_response,
    decode_credential,
    encode_credential,
)
from app.security.crypto import SecretBox

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
PUBLIC_BASE = "https://jarvis.example.com"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "jarvis_env": "test",
        "encryption_key": Fernet.generate_key().decode("ascii"),
        "public_base_url": PUBLIC_BASE + "/",
        "google_oauth_client_id": "google-client-id.apps.example",
        "google_oauth_client_secret": "google-client-secret-value",
        "github_oauth_client_id": "github-client-id",
        "github_oauth_client_secret": "github-client-secret-value",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _box() -> SecretBox:
    return SecretBox(Fernet.generate_key().decode("ascii"))


def test_pkce_s256_matches_rfc7636_appendix_b_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_generated_verifier_is_within_rfc7636_length_and_alphabet() -> None:
    verifier = new_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert new_code_verifier() != verifier


def test_state_round_trip_for_the_same_user_and_provider() -> None:
    box = _box()
    user = uuid.uuid4()
    verifier = new_code_verifier()
    token = issue_state(box, user_id=user, provider_key="github", verifier=verifier, ttl_seconds=600, now=NOW)
    payload = open_state(box, token, provider_key="github", user_id=user, now=NOW + timedelta(seconds=30))
    assert payload.v == verifier
    assert payload.u == user
    assert payload.p == "github"


def test_state_does_not_expose_the_verifier_in_clear() -> None:
    box = _box()
    verifier = new_code_verifier()
    token = issue_state(box, user_id=uuid.uuid4(), provider_key="github", verifier=verifier, ttl_seconds=600, now=NOW)
    assert verifier not in token


def test_tampered_state_is_rejected_as_invalid() -> None:
    box = _box()
    user = uuid.uuid4()
    token = issue_state(box, user_id=user, provider_key="github", verifier=new_code_verifier(), ttl_seconds=600, now=NOW)
    middle = len(token) // 2
    flipped = token[:middle] + ("A" if token[middle] != "A" else "B") + token[middle + 1 :]
    with pytest.raises(IntegrationError) as exc:
        open_state(box, flipped, provider_key="github", user_id=user, now=NOW)
    assert exc.value.code == "invalid_state"


def test_state_sealed_with_another_key_is_rejected() -> None:
    user = uuid.uuid4()
    token = issue_state(_box(), user_id=user, provider_key="github", verifier=new_code_verifier(), ttl_seconds=600, now=NOW)
    with pytest.raises(IntegrationError) as exc:
        open_state(_box(), token, provider_key="github", user_id=user, now=NOW)
    assert exc.value.code == "invalid_state"


def test_expired_state_is_rejected() -> None:
    box = _box()
    user = uuid.uuid4()
    token = issue_state(box, user_id=user, provider_key="github", verifier=new_code_verifier(), ttl_seconds=600, now=NOW)
    with pytest.raises(IntegrationError) as exc:
        open_state(box, token, provider_key="github", user_id=user, now=NOW + timedelta(seconds=600))
    assert exc.value.code == "state_expired"


def test_state_for_another_provider_is_invalid() -> None:
    box = _box()
    user = uuid.uuid4()
    token = issue_state(box, user_id=user, provider_key="github", verifier=new_code_verifier(), ttl_seconds=600, now=NOW)
    with pytest.raises(IntegrationError) as exc:
        open_state(box, token, provider_key="google_calendar", user_id=user, now=NOW)
    assert exc.value.code == "invalid_state"


def test_state_for_another_user_is_rejected() -> None:
    box = _box()
    owner = uuid.uuid4()
    token = issue_state(box, user_id=owner, provider_key="github", verifier=new_code_verifier(), ttl_seconds=600, now=NOW)
    with pytest.raises(IntegrationError) as exc:
        open_state(box, token, provider_key="github", user_id=uuid.uuid4(), now=NOW)
    assert exc.value.code == "state_user_mismatch"


def test_empty_or_oversized_state_is_invalid_without_decrypting() -> None:
    box = _box()
    with pytest.raises(IntegrationError) as empty:
        open_state(box, "", provider_key="github", user_id=uuid.uuid4(), now=NOW)
    with pytest.raises(IntegrationError) as huge:
        open_state(box, "x" * 5000, provider_key="github", user_id=uuid.uuid4(), now=NOW)
    assert empty.value.code == huge.value.code == "invalid_state"


def test_google_authorization_url_requests_offline_access_pkce_and_exact_redirect() -> None:
    settings = _settings()
    challenge = code_challenge(new_code_verifier())
    url = build_authorization_url(settings, GOOGLE_CALENDAR, state="state-token", challenge=challenge)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert query["client_id"] == ["google-client-id.apps.example"]
    assert query["redirect_uri"] == [f"{PUBLIC_BASE}/api/v1/integrations/google_calendar/callback"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge"] == [challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["scope"] == [" ".join(GOOGLE_CALENDAR.scopes)]
    assert "openid" in query["scope"][0].split() and "email" in query["scope"][0].split()
    assert "https://www.googleapis.com/auth/calendar.readonly" in query["scope"][0].split()
    assert "scope=openid%20email%20" in parts.query


def test_github_authorization_url_has_no_offline_parameters() -> None:
    settings = _settings()
    url = build_authorization_url(settings, GITHUB, state="s", challenge=code_challenge(new_code_verifier()))
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "github.com"
    assert query["scope"] == ["read:user"]
    assert query["code_challenge_method"] == ["S256"]
    assert "access_type" not in query and "prompt" not in query
    assert query["redirect_uri"] == [f"{PUBLIC_BASE}/api/v1/integrations/github/callback"]


def test_redirect_uri_is_built_only_from_public_base_url() -> None:
    settings = _settings(public_base_url="https://app.example.org///")
    assert redirect_uri(settings, GITHUB) == "https://app.example.org/api/v1/integrations/github/callback"
    assert redirect_uri(settings, GOOGLE_CALENDAR).endswith("/api/v1/integrations/google_calendar/callback")


def test_begin_connect_returns_url_without_exposing_the_verifier() -> None:
    settings = _settings()
    box = _box()
    url = begin_connect(settings, box, GITHUB, user_id=uuid.uuid4(), now=NOW)
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert "code_verifier" not in url
    assert len(state) > 100


def test_begin_connect_refuses_when_provider_is_not_configured() -> None:
    settings = _settings(github_oauth_client_secret=None)
    with pytest.raises(IntegrationError) as exc:
        begin_connect(settings, _box(), GITHUB, user_id=uuid.uuid4(), now=NOW)
    assert exc.value.code == "not_configured"
    assert exc.value.status_code == 409


def test_a_provider_is_configured_only_when_both_values_are_set() -> None:
    only_id = _settings(github_oauth_client_secret=None)
    only_secret = _settings(github_oauth_client_id="  ")
    both = _settings()
    assert not is_configured(only_id, GITHUB)
    assert not is_configured(only_secret, GITHUB)
    assert is_configured(both, GITHUB)
    assert credentials(both, GITHUB) == ("github-client-id", "github-client-secret-value")


def test_provider_registry_is_fixed_and_rejects_unknown_keys() -> None:
    assert get_provider("github") is GITHUB
    assert get_provider("google_calendar") is GOOGLE_CALENDAR
    assert get_provider("discord") is None
    assert get_provider("https://evil.example") is None


def test_parse_scopes_accepts_space_and_comma_separators_and_drops_duplicates() -> None:
    assert parse_scopes("openid email openid") == ["openid", "email"]
    assert parse_scopes("read:user,gist") == ["read:user", "gist"]
    assert parse_scopes("") == []
    assert parse_scopes(None) == []


def test_credential_round_trip_keeps_fields_and_hides_the_token_in_repr() -> None:
    box = _box()
    credential = Credential(
        access_token="access-token-value-123",
        refresh_token="refresh-token-value-456",
        expires_at=NOW + timedelta(hours=1),
        scope="openid email",
    )
    ciphertext = encode_credential(box, credential)
    assert "access-token-value-123" not in ciphertext
    assert "refresh-token-value-456" not in ciphertext
    restored = decode_credential(box, ciphertext)
    assert restored.access_token == "access-token-value-123"
    assert restored.refresh_token == "refresh-token-value-456"
    assert restored.expires_at == NOW + timedelta(hours=1)
    assert restored.scope == "openid email"
    assert "access-token-value-123" not in repr(restored)


def test_undecryptable_credential_asks_for_reconnect_without_detail() -> None:
    ciphertext = encode_credential(
        _box(), Credential("access-token-value-123", None, None, "")
    )
    with pytest.raises(IntegrationError) as exc:
        decode_credential(_box(), ciphertext)
    assert exc.value.code == "reconnect_required"
    assert "access-token-value-123" not in exc.value.message


def test_token_response_without_refresh_or_expiry_keeps_previous_values_on_refresh() -> None:
    previous = Credential("old-access", "kept-refresh", NOW, "openid email")
    refreshed = credential_from_token_response(
        {"access_token": "new-access", "expires_in": 3600, "token_type": "Bearer"},
        now=NOW,
        previous=previous,
    )
    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "kept-refresh"
    assert refreshed.expires_at == NOW + timedelta(seconds=3600)
    assert refreshed.scope == "openid email"


def test_github_style_token_response_has_no_expiry() -> None:
    credential = credential_from_token_response(
        {"access_token": "gho_value", "scope": "read:user", "token_type": "bearer"}, now=NOW
    )
    assert credential.expires_at is None
    assert credential.refresh_token is None
    assert credential.scope == "read:user"


def test_account_label_must_be_a_short_string() -> None:
    assert account_label_from_body(GOOGLE_CALENDAR, {"email": " ada@example.com "}) == "ada@example.com"
    for body in ({"email": ""}, {"email": 5}, {"email": "x" * 201}, {}, None):
        with pytest.raises(IntegrationError) as exc:
            account_label_from_body(GOOGLE_CALENDAR, body)
        assert exc.value.code == "provider_error"
