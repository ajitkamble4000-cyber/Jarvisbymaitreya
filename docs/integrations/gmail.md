# Gmail integration (OAuth)

Status: **implemented** (OAuth 2.0 authorization code with PKCE, read-only, metadata only). Google was **not** exercised from the build environment: every Gmail call in the tests goes to a mocked transport. Treat the first live connection as the real verification.

Provider id in the API: `gmail`. The catalogue entry is "Gmail". It uses the same Google client as Google Calendar and Google Drive.

## What it does

* Connect a Google account with read-only Gmail access.
* Tool `gmail_recent_messages`: lists recent messages that match a Gmail search. The default search is `is:unread`. Up to 10 messages per call (default 5). For each message it returns only the sender (`from`, at most 120 characters), the subject (at most 200) and the date (at most 60).
* Connection test (`POST /api/v1/integrations/gmail/test`) and disconnect (`DELETE /api/v1/integrations/gmail/connection`).

Message bodies and snippets are never requested or returned. Sending, labelling, archiving and deleting mail are not part of this integration.

## Setup

1. In Google Cloud Console, select the project that holds the JARVIS OAuth client, and enable the **Gmail API**.
2. Configure the **OAuth consent screen**. Add the scopes `openid`, `email` and `https://www.googleapis.com/auth/gmail.readonly`.
3. In the **Web application** OAuth client, add this **Authorized redirect URI**, built from your `PUBLIC_BASE_URL`. It must match exactly:

   `{PUBLIC_BASE_URL}/api/v1/integrations/gmail/callback`

   Google Calendar and Google Drive each need their own redirect URI in the same client.
4. Set the environment variables and restart the API. Gmail uses the Google client that Calendar uses:

   * `GOOGLE_OAUTH_CLIENT_ID`
   * `GOOGLE_OAUTH_CLIENT_SECRET`

   The provider is offered only when both are set. Otherwise `GET /api/v1/integrations` reports `requires_configuration` and connect returns `409 not_configured`.
5. Sign in to JARVIS, open Integrations, and connect Gmail. Google asks for consent. JARVIS then returns to `/integrations?provider=gmail&status=connected`.

## Scopes requested

* `openid`
* `email`
* `https://www.googleapis.com/auth/gmail.readonly`

The request uses `access_type=offline` and `prompt=consent`, so Google issues a refresh token on every connect. After the callback, JARVIS checks that `gmail.readonly` was granted. If it was not, nothing is stored, the grant is revoked at Google, and the redirect reports `reason=missing_scope`.

## Tool: `gmail_recent_messages`

Arguments (any other key is refused):

| Field | Type | Rule | Default |
|---|---|---|---|
| `query` | string | 1 to 100 characters, no control characters | `is:unread` |
| `max_results` | integer | 1 to 10 | 5 |

Output: `{summary, source: "gmail", count, messages: [{from, subject, date}], note}`. A subject that is empty is returned as `(no subject)`. The request to Google is `messages.list` followed by one `messages.get` per message with `format=metadata` and only the `From`, `Subject` and `Date` headers.

Risk MEDIUM, category `communication`, no approval needed for a read.

## How tokens are handled

* The access token and refresh token are stored only as one Fernet ciphertext in the `integrations` table, encrypted with `ENCRYPTION_KEY`. They are never returned, never placed in events, and never logged.
* A token that expires within 60 seconds is refreshed before a provider call.
* If Google returns `invalid_grant`, or there is no refresh token, the connection becomes `expired` with `last_error_code: reconnect_required`.
* Disconnect calls Google's revoke endpoint with the refresh token. The local copy is deleted whether or not Google confirms, and the response says which happened.

## Honest limits

* Not verified against live Google. The OAuth flow, refresh and Gmail calls are tested against a scripted transport only.
* Google classifies `gmail.readonly` as a restricted scope. Production use beyond a small group needs Google's app verification and its security assessment. An app in "Testing" status also expires refresh tokens after about seven days.
* The metadata response from Google still contains a `snippet` field. The handler never reads or returns it, but Google sends it.
* The callback URL carries the `code` and `state` query parameters. Uvicorn's access log records the full request line by default, so the one-time code can reach container logs unless the access log is redacted for `/api/v1/integrations/*/callback`. Tokens are not in that URL.
* At most 10 messages per call and no pagination. No attachments, no bodies, no sending.
* The browser flow, the redirect reasons and the endpoints are described in `docs/phase3/integrations.md`.
