# Google Drive integration (OAuth)

Status: **implemented** (OAuth 2.0 authorization code with PKCE, read-only, names and metadata only). Google was **not** exercised from the build environment: every Drive call in the tests goes to a mocked transport. Treat the first live connection as the real verification.

Provider id in the API: `google_drive`. The catalogue entry is "Google Drive". It uses the same Google client as Google Calendar and Gmail.

## What it does

* Connect a Google account with read-only metadata access to Drive.
* Tool `drive_search_files`: finds files whose name contains the given text, excluding trashed files. Up to 20 results (default 10). Each result has `name`, `mimeType`, `modifiedTime` and `webViewLink`.
* Connection test (`POST /api/v1/integrations/google_drive/test`) and disconnect (`DELETE /api/v1/integrations/google_drive/connection`).

File contents, owners, sharing settings and file ids are not returned. Uploading, editing, moving and sharing files are not part of this integration.

## Setup

1. In Google Cloud Console, select the project that holds the JARVIS OAuth client, and enable the **Google Drive API**.
2. Configure the **OAuth consent screen**. Add the scopes `openid`, `email` and `https://www.googleapis.com/auth/drive.metadata.readonly`.
3. In the **Web application** OAuth client, add this **Authorized redirect URI**, built from your `PUBLIC_BASE_URL`. It must match exactly:

   `{PUBLIC_BASE_URL}/api/v1/integrations/google_drive/callback`

4. Set the environment variables and restart the API. Drive uses the Google client that Calendar uses:

   * `GOOGLE_OAUTH_CLIENT_ID`
   * `GOOGLE_OAUTH_CLIENT_SECRET`

   The provider is offered only when both are set. Otherwise `GET /api/v1/integrations` reports `requires_configuration` and connect returns `409 not_configured`.
5. Sign in to JARVIS, open Integrations, and connect Google Drive. JARVIS then returns to `/integrations?provider=google_drive&status=connected`.

## Scopes requested

* `openid`
* `email`
* `https://www.googleapis.com/auth/drive.metadata.readonly`

`drive.metadata.readonly` is the narrowest read scope that can search by name. It does not allow reading file content. The request uses `access_type=offline` and `prompt=consent`. After the callback, JARVIS checks that the metadata scope was granted, as for Calendar and Gmail.

## Tool: `drive_search_files`

Arguments (any other key is refused):

| Field | Type | Rule | Default |
|---|---|---|---|
| `name_contains` | string | 1 to 100 characters, no control characters, not blank | required |
| `max_results` | integer | 1 to 20 | 10 |

The query sent to Drive is `name contains '<name>' and trashed = false`. Before the name is placed in the quoted literal, every backslash is doubled and every single quote is escaped with a backslash. A name such as `x' or trashed = true or name = '` therefore stays one literal and cannot add clauses or change the trashed filter. The request asks for `files(id,name,mimeType,modifiedTime,webViewLink)` and a page size equal to `max_results`.

Output: `{summary, source: "google_drive", count, files: [{name, mimeType, modifiedTime, webViewLink}], note}`. `webViewLink` is returned only when it is an `https://` link; any other value is dropped. Names are cut to 200 characters.

Risk MEDIUM, category `files`, no approval needed for a read.

## How tokens are handled

Same as Google Calendar and Gmail. One Fernet ciphertext in the `integrations` table, refresh 60 seconds before expiry, `invalid_grant` leads to `reconnect_required`, and disconnect revokes at Google before the local copy is deleted.

## Honest limits

* Not verified against live Google. The OAuth flow, refresh and Drive calls are tested against a scripted transport only.
* Drive's `contains` operator matches the way Drive matches names. Results depend on what Drive returns for that query, and only for files the connected account can see.
* Google's classification of `drive.metadata.readonly` must be checked on Google's current scope list before production use. Production use beyond a small group may need Google's app verification. An app in "Testing" status expires refresh tokens after about seven days.
* The callback URL carries the `code` and `state` query parameters, which the access log can record. See the Gmail document for the details.
* The browser flow, the redirect reasons and the endpoints are described in `docs/phase3/integrations.md`.
