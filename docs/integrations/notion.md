# Notion integration (OAuth)

Status: **implemented** (OAuth 2.0 authorization code, read-only search). Notion was **not** exercised from the build environment: every Notion call in the tests goes to a mocked transport. Treat the first live connection as the real verification. Notion's PKCE behaviour is **unverified** (see the limits below).

Provider id in the API: `notion`. The catalogue entry is "Notion". Notion has its own client id and secret; it does not use the Google client.

## What it does

* Connect a Notion workspace. The user chooses which pages and databases the JARVIS integration can see on Notion's consent screen.
* Tool `notion_search`: searches those pages and databases by title. Up to 20 results (default 10). Each result has `title`, `object` (`page` or `database`) and `url`.
* Connection test (`POST /api/v1/integrations/notion/test`) and disconnect (`DELETE /api/v1/integrations/notion/connection`).

Page content, comments and users are not read. Editing pages is not part of this integration.

## Setup

1. In Notion, go to **my integrations** (https://www.notion.so/my-integrations) and create a **public** integration. Under OAuth, set the **Redirect URI** to this value, built from your `PUBLIC_BASE_URL`. It must match exactly:

   `{PUBLIC_BASE_URL}/api/v1/integrations/notion/callback`

2. Copy the **OAuth client ID** and **OAuth client secret** from the integration's settings.
3. Set the environment variables and restart the API:

   * `NOTION_OAUTH_CLIENT_ID`
   * `NOTION_OAUTH_CLIENT_SECRET`

   The provider is offered only when both are set. Otherwise `GET /api/v1/integrations` reports `requires_configuration` and connect returns `409 not_configured`.
4. Sign in to JARVIS, open Integrations, and connect Notion. Notion asks which workspace and which pages to share. JARVIS then returns to `/integrations?provider=notion&status=connected`.

## How the flow works

* The authorization request has `owner=user`, `response_type=code`, the `state` value, and the PKCE parameters `code_challenge` and `code_challenge_method=S256`. No `scope` parameter is sent, because Notion does not take one.
* The token request is a JSON body with `grant_type`, `code`, `redirect_uri` and `code_verifier`. The client id and secret are sent as HTTP Basic auth, not in the body.
* The account label is the `workspace_name` from the token response. No account call is made at connect time.

## Scopes and capabilities

Notion grants access per page or database, not by OAuth scope strings. The token response carries no scope list, so the scope check used for Google does not apply. The catalogue lists `read_content` as the capability this integration relies on. The integration's capabilities are set by the workspace owner in Notion's integration settings. JARVIS only searches. It does not ask for or check any capability at runtime.

## Tool: `notion_search`

Arguments (any other key is refused):

| Field | Type | Rule | Default |
|---|---|---|---|
| `query` | string | 1 to 100 characters, no control characters, not blank | required |
| `max_results` | integer | 1 to 20 | 10 |

The request is `POST https://api.notion.com/v1/search` with the headers `Notion-Version: 2022-06-28` and `Authorization: Bearer <token>`, and the body `{query, page_size}`. Only results of type `page` or `database` with an `https://` URL are returned. The title is read from the title property (pages) or the title (databases) and cut to 200 characters.

Output: `{summary, source: "notion", count, results: [{title, object, url}], note}`.

Risk LOW, category `productivity`, no approval needed for a read.

## How tokens are handled

* The access token is stored only as one Fernet ciphertext in the `integrations` table, encrypted with `ENCRYPTION_KEY`. It is never returned, never placed in events, and never logged.
* Notion issues no refresh token in this flow, and no expiry. A token stays valid until it is revoked. If Notion refuses a call with 401, the connection becomes `expired` with `last_error_code: reconnect_required`.
* Disconnect deletes the local copy only. JARVIS does not revoke Notion tokens in this flow, so the connection stays active at Notion until you remove JARVIS from your Notion workspace connections. The response says so.
* Connecting again replaces the stored token. The previous token is not revoked for the same reason.

## Honest limits

* Not verified against live Notion. The OAuth flow, the Basic-auth token request and the search calls are tested against a scripted transport only.
* **PKCE is unverified.** Notion does not document PKCE. JARVIS sends the PKCE parameters anyway. Whether Notion checks them is not known. The exchange is protected by the client secret in any case.
* Search only returns pages and databases the user shared with the integration. Results depend on what Notion returns for the query.
* `Notion-Version` is pinned to `2022-06-28`. Other API versions are not tested.
* Titles only: page content is not read, and no blocks, comments or users are returned.
* The callback URL carries the `code` and `state` query parameters, which the access log can record. See the Gmail document for the details.
* The browser flow, the redirect reasons and the endpoints are described in `docs/phase3/integrations.md`.
