# Phase 2: OAuth integrations (contract for the web and verification agents)

Scope: Google Calendar (`google_calendar`) and GitHub (`github`), connected with OAuth 2.0 authorization code and PKCE. Both providers were tested against a mocked transport only. Neither was exercised against the live provider from the build environment. Per-provider setup is in `docs/integrations/google_calendar.md` and `docs/integrations/github.md`. The rest of the API contract is in `docs/api.md`, which this document does not edit.

## Environment

| Variable | Purpose |
| --- | --- |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Google Calendar. Both are required; otherwise the provider is `requires_configuration`. |
| `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` | GitHub. Both are required. |
| `PUBLIC_BASE_URL` | Base of every redirect: `{PUBLIC_BASE_URL}/api/v1/integrations/{provider}/callback`. Must be `https` in production. |
| `ENCRYPTION_KEY` | Encrypts stored tokens. Required in production. |
| `OAUTH_STATE_TTL_SECONDS` | Lifetime of the sign-in state, 60 to 1800, default 600. |

Provider endpoints and scopes are constants in `app/integrations/oauth/providers.py`. No request parameter can change them.

## Endpoints

All routes sit under `/api/v1`. Every route except the callback needs a session cookie. Unsafe methods (`POST`, `DELETE`) also need the `X-CSRF-Token` header that matches the `jarvis_csrf` cookie.

### `GET /integrations`

Returns the catalogue. Existing fields are unchanged. New fields are `permissions`, `account_label`, `last_checked_at`, `last_error_code` and `last_error_message`.

```json
{
  "items": [
    {
      "id": "google_calendar",
      "name": "Google Calendar",
      "category": "Productivity",
      "status": "connected",
      "implemented": true,
      "requires_configuration": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"],
      "scopes_requested": ["openid", "email", "https://www.googleapis.com/auth/calendar.readonly"],
      "permissions": ["openid", "email", "https://www.googleapis.com/auth/calendar.readonly"],
      "docs": "docs/integrations/google_calendar.md",
      "account_label": "ada@example.com",
      "last_checked_at": "2026-10-20T10:00:00+00:00",
      "last_error_code": null,
      "last_error_message": null
    }
  ]
}
```

`status` values: `planned` (not implemented), `requires_configuration` (client settings missing), `not_connected`, `connected`, `expired` (reconnect needed), `error` (last check failed), `disconnected`. The other five catalogue entries are `planned` and `implemented: false`.

Only `google_calendar` and `github` are valid provider ids. Anything else returns `404 unknown_integration`.

### `POST /integrations/{provider}/connect`

Rate limit: 20 per minute per user (`429 rate_limited`).

Response `200`: `{"authorization_url": "https://..."}`. The browser must navigate to this URL (`window.location.assign`). The PKCE verifier is inside the encrypted `state` parameter and is never returned separately.

Errors: `404 unknown_integration`, `409 not_configured`.

### `GET /integrations/{provider}/callback?code=&state=`

Called by the provider's redirect. The response is always a `302` to the web app, never JSON:

* Success: `{PUBLIC_BASE_URL}/integrations?provider=<id>&status=connected`
* Failure: `{PUBLIC_BASE_URL}/integrations?provider=<id>&status=error&reason=<code>`. For an unknown provider the `provider` parameter is omitted.

Reason codes: `not_signed_in`, `invalid_state`, `state_expired`, `state_user_mismatch`, `provider_denied` (the user declined), `invalid_callback`, `missing_scope` (the provider did not grant a permission JARVIS needs, such as the calendar scope; nothing is stored and the grant is revoked when the provider supports it), `exchange_failed`, `provider_unavailable`, `provider_error`, `not_configured`, `rate_limited` (30 per minute per client IP), `unknown_integration`.

Rate limits and reasons never contain provider text or tokens. Redirects carry only these fixed values.

### `POST /integrations/{provider}/test`

Rate limit: 20 per minute per user. Refreshes the token if it expires within 60 seconds, then makes the provider's cheap read (Google userinfo or GitHub user).

Response `200` (also when the provider check fails, with `ok: false`):

```json
{"ok": true, "status": "connected", "account_label": "ada@example.com", "message": "The provider accepted the connection."}
```

Errors: `404 unknown_integration`, `409 not_connected`, `409 not_configured`.

### `DELETE /integrations/{provider}/connection`

Google: revokes the refresh token at Google, then deletes the local credential. GitHub: deletes the local credential only. Idempotent when nothing is connected.

Response `200`:

```json
{"status": "disconnected", "provider_revoked": true, "message": "Access was revoked at the provider and the stored token was deleted."}
```

For GitHub, `provider_revoked` is `false`, and the message says the grant stays active until the user removes JARVIS under GitHub Settings > Applications.

Errors: `404 unknown_integration`.

## Error codes (JSON errors, standard envelope)

| HTTP | `error.code` | Meaning |
| --- | --- | --- |
| 404 | `unknown_integration` | Provider id is not a registered implemented provider. |
| 409 | `not_configured` | Client settings are missing. |
| 409 | `not_connected` | No stored connection for this user. |
| 409 | `reconnect_required` | Token was rejected or refresh was refused. Used by tools. |
| 429 | `rate_limited` | Per-user or per-IP limit reached. |
| 502 | `provider_unavailable` | Network error, timeout, 429 or 5xx from the provider. |
| 502 | `provider_error` | Provider returned an unexpected response. |

## Tools (server target, no approval required)

Both tools are registered with `register_server_handler`. Their output is untrusted text, and each result carries a `note` saying so.

### `calendar_upcoming_events`

* Category `productivity`, risk `medium`, `approval_required: false`.
* Args: `{"days": int 1..14 (default 7), "max_results": int 1..20 (default 10)}`. Other keys are refused.
* Output: `{"summary", "source": "google_calendar", "count", "events": [{"start", "end", "summary"}], "note"}`. Summary text is at most 200 characters. Cancelled events are skipped. Locations, attendees and descriptions are never returned. All-day events use `date` values.

### `github_list_repositories`

* Category `development`, risk `low`, `approval_required: false`.
* Args: `{"max_results": int 1..20 (default 10)}`.
* Output: `{"summary", "source": "github", "count", "repositories": [{"name", "html_url", "updated_at", "description"}], "note"}`. Public repositories only, most recently updated first. Descriptions are at most 200 characters.

### Refusals

When a connection is missing, a tool task is created with status `failed` and `error.code` set to `not_connected`, `not_configured`, `reconnect_required` or `provider_unavailable`. No provider call is made when the connection is missing. Invalid arguments are refused before a task exists, with `invalid_arguments`.

The tool's `status` in `GET /tools` is `working`, or `requires_configuration` when the provider's client settings are missing.

## Audit

Actions written to `audit_events` (never containing tokens or codes): `integration.connect` (outcome `success`, or `failure` with `data.reason`), `integration.test` (`data.status`), `integration.disconnect` (`data.provider_revoked`).

## Notes for the web agent

* The Integrations page currently says "no connect button". Once wired, the connect button calls `POST .../connect` with the CSRF header and then navigates to `authorization_url`.
* Read `provider` and `status` from the callback redirect's query string on `/integrations`. Map `reason` to text. Do not display provider text, because none is sent.
* `expired` means "Reconnect". `error` means "Test again". `requires_configuration` means "Set up the provider on the server".

## Notes for the verification agent

* Tests: `tests/unit/test_oauth_flow.py` and `tests/integration/test_integrations_oauth.py`. No live provider is called.
* The callback URL contains `code` and `state`, and Uvicorn's access log records the query string by default. Check that container logs do not contain the `code` value of a real callback. This is an open item (see the limitations below).

## Honest limits

* Google and GitHub were not called live. Every provider call is tested against a scripted transport.
* GitHub disconnect does not revoke the token at GitHub.
* Uvicorn's access log records the callback query string (`code` and `state`). This is not changed here because the server entry point is not owned by this work.
* The explore and analytics services still describe these integrations as planned or unimplemented, and so do parts of an older test suite. See the contract requests in the workstream report.
