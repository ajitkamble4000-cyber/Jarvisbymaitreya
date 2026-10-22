# Google Calendar integration (OAuth)

Status: **implemented** (OAuth 2.0 authorization code with PKCE, read-only). Google itself was **not** exercised from the build environment: every Google call in the tests goes to a mocked transport. Treat the first live connection as the real verification.

Provider id in the API: `google_calendar`. The catalogue entry is "Google Calendar". Gmail and Google Drive are still planned and use different scopes.

## What it does

* Connect a Google account with read-only calendar access.
* Tool `calendar_upcoming_events`: lists the next 1 to 14 days of events (start, end, title). Locations and attendees are never returned.
* Connection test (`POST /api/v1/integrations/google_calendar/test`) and disconnect (`DELETE /api/v1/integrations/google_calendar/connection`).

Creating or changing events is not part of this integration.

## Setup

1. In Google Cloud Console, select or create a project and enable the **Google Calendar API**.
2. Configure the **OAuth consent screen**. Add the scopes `openid`, `email` and `https://www.googleapis.com/auth/calendar.readonly`.
3. Create an **OAuth client ID** of type **Web application**.
4. Add this **Authorized redirect URI**, built from your `PUBLIC_BASE_URL`. It must match exactly:

   `{PUBLIC_BASE_URL}/api/v1/integrations/google_calendar/callback`

   For example: `https://jarvis.example.com/api/v1/integrations/google_calendar/callback`. Production requires `https`.
5. Set the environment variables and restart the API:

   * `GOOGLE_OAUTH_CLIENT_ID`
   * `GOOGLE_OAUTH_CLIENT_SECRET`

   The provider is offered only when both are set. Otherwise `GET /api/v1/integrations` reports `requires_configuration` and connect returns `409 not_configured`.
6. Sign in to JARVIS, open Integrations, and connect Google Calendar. Google asks for consent. JARVIS then returns to `/integrations?provider=google_calendar&status=connected`.

## Scopes requested

* `openid`
* `email`
* `https://www.googleapis.com/auth/calendar.readonly`

The request uses `access_type=offline` and `prompt=consent`, so Google issues a refresh token on every connect.

## How tokens are handled

* The access token and refresh token are stored only as one Fernet ciphertext in the `integrations` table, encrypted with `ENCRYPTION_KEY`. They are never returned by the API, never placed in events, and never logged.
* A token that expires within 60 seconds is refreshed before a provider call.
* If Google returns `invalid_grant`, or there is no refresh token, the connection becomes `expired` with `last_error_code: reconnect_required`. The user must connect again.
* Disconnect calls Google's revoke endpoint with the refresh token. The local credential is deleted whether or not Google confirms the revocation, and the response says which happened.

## Honest limits

* Not verified against live Google. The OAuth flow, refresh and Calendar calls are tested against a scripted transport only.
* `calendar.readonly` is a sensitive scope. A consent screen in "Testing" status allows only listed test users, and Google expires refresh tokens for apps in that status after about seven days. Production use beyond a small group needs Google's app verification.
* The callback URL carries the `code` and `state` query parameters. Uvicorn's access log records the full request line by default, so those values can reach container logs unless the access log is disabled or redacted for `/api/v1/integrations/*/callback`.
* Read-only. No event writes, no Gmail, no Drive.
* The browser flow, the redirect reasons and the endpoints are described in `docs/phase2/integrations.md`.
