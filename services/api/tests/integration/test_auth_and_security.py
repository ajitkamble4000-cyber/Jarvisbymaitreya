"""Authentication, session, CSRF/origin enforcement and security headers against real PostgreSQL."""

from httpx import AsyncClient

from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers


async def test_status_requires_setup_before_any_user_exists(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/status")
    assert response.status_code == 200
    assert response.json() == {"setup_required": True, "authenticated": False, "user": None}
    assert "jarvis_csrf" in response.cookies or client.cookies.get("jarvis_csrf")


async def test_setup_creates_owner_and_session(client: AsyncClient) -> None:
    owner = await create_owner(client)
    assert owner["email"] == "owner@example.com"
    assert owner["role"] == "owner"
    assert "password_hash" not in owner

    status = (await client.get("/api/v1/auth/status")).json()
    assert status["setup_required"] is False
    assert status["authenticated"] is True
    assert status["user"]["email"] == "owner@example.com"


async def test_setup_is_refused_once_a_user_exists(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.post(
        "/api/v1/auth/setup",
        json={"email": "other@example.com", "password": "another-long-password"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "setup_complete"


async def test_setup_rejects_short_passwords_and_bad_emails(client: AsyncClient) -> None:
    short = await client.post("/api/v1/auth/setup", json={"email": "a@example.com", "password": "short"})
    assert short.status_code == 422
    assert short.json()["error"]["code"] == "validation_error"
    bad_email = await client.post(
        "/api/v1/auth/setup", json={"email": "not-an-email", "password": "long-enough-password"}
    )
    assert bad_email.status_code == 422
    assert "long-enough-password" not in bad_email.text


async def test_login_failure_is_generic_and_success_sets_cookies(client: AsyncClient) -> None:
    await create_owner(client)
    await client.post("/api/v1/auth/logout", headers=csrf_headers(client))

    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password-value"},
        headers=csrf_headers(client),
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password-value"},
        headers=csrf_headers(client),
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    ok = await client.post(
        "/api/v1/auth/login",
        json={"email": "OWNER@example.com", "password": "correct-horse-battery"},
        headers=csrf_headers(client),
    )
    assert ok.status_code == 200
    assert "jarvis_session" in client.cookies


async def test_session_cookie_is_httponly_and_csrf_cookie_is_readable(client: AsyncClient) -> None:
    await create_owner(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
        headers=csrf_headers(client),
    )
    session_cookie = [h for h in login.headers.get_list("set-cookie") if h.startswith("jarvis_session=")]
    csrf_cookie_header = [h for h in login.headers.get_list("set-cookie") if h.startswith("jarvis_csrf=")]
    assert session_cookie and "HttpOnly" in session_cookie[0] and "SameSite=lax" in session_cookie[0]
    assert csrf_cookie_header and "HttpOnly" not in csrf_cookie_header[0]


async def test_unsafe_request_with_session_cookie_needs_matching_csrf_token(client: AsyncClient) -> None:
    await create_owner(client)
    no_token = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "a-brand-new-password"},
    )
    assert no_token.status_code == 403
    assert no_token.json()["error"]["code"] == "csrf_failed"

    wrong_token = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "a-brand-new-password"},
        headers={"X-CSRF-Token": "not-the-token"},
    )
    assert wrong_token.json()["error"]["code"] == "csrf_failed"

    with_token = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "a-brand-new-password"},
        headers=csrf_headers(client),
    )
    assert with_token.status_code == 204


async def test_cross_origin_unsafe_requests_are_rejected(client: AsyncClient) -> None:
    await create_owner(client)
    response = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "a-brand-new-password"},
        headers={**csrf_headers(client), "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


async def test_password_change_revokes_other_sessions(client: AsyncClient) -> None:
    await create_owner(client)
    other = AsyncClient(base_url="http://testserver", headers={"Origin": TEST_ORIGIN}, transport=client._transport)
    login = await other.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
        headers=csrf_headers(other),
    )
    assert login.status_code == 200
    await client.post(
        "/api/v1/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "a-brand-new-password"},
        headers=csrf_headers(client),
    )
    assert (await other.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_session_listing_and_revocation(client: AsyncClient) -> None:
    await create_owner(client)
    sessions = (await client.get("/api/v1/auth/sessions")).json()
    assert len(sessions) == 1 and sessions[0]["current"] is True
    response = await client.delete(f"/api/v1/auth/sessions/{sessions[0]['id']}", headers=csrf_headers(client))
    assert response.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_logout_invalidates_session(client: AsyncClient) -> None:
    await create_owner(client)
    assert (await client.post("/api/v1/auth/logout", headers=csrf_headers(client))).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_health_and_security_headers(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
