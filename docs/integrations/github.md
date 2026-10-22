# GitHub integration (OAuth)

Status: **implemented** (OAuth 2.0 authorization code with PKCE, read-only). GitHub was **not** exercised from the build environment: every GitHub call in the tests goes to a mocked transport. Treat the first live connection as the real verification.

Provider id in the API: `github`.

## What it does

* Connect a GitHub account with the `read:user` scope.
* Tool `github_list_repositories`: lists the account's most recently updated **public** repositories, with name, URL, last update and description (up to 200 characters).
* Connection test (`POST /api/v1/integrations/github/test`) and disconnect (`DELETE /api/v1/integrations/github/connection`).

Issues, pull requests, private repositories and any write access are not part of this version.

## Setup

1. On GitHub, open **Settings > Developer settings > OAuth Apps > New OAuth App**.
2. Set **Homepage URL** to your `PUBLIC_BASE_URL`.
3. Set **Authorization callback URL** to this value. It must match exactly:

   `{PUBLIC_BASE_URL}/api/v1/integrations/github/callback`

   For example: `https://jarvis.example.com/api/v1/integrations/github/callback`.
4. Create the app, then generate a **client secret**.
5. Set the environment variables and restart the API:

   * `GITHUB_OAUTH_CLIENT_ID`
   * `GITHUB_OAUTH_CLIENT_SECRET`

   The provider is offered only when both are set. Otherwise `GET /api/v1/integrations` reports `requires_configuration` and connect returns `409 not_configured`.
6. Sign in to JARVIS and connect GitHub from the Integrations page. JARVIS then returns to `/integrations?provider=github&status=connected`.

## Scope requested

* `read:user`: read the account login used as the label.

## How tokens are handled

* GitHub does not expire OAuth access tokens by default and does not issue refresh tokens. The token stays valid until it is revoked. A token that GitHub rejects with 401 makes the connection `expired` with `last_error_code: reconnect_required`.
* The token is stored only as an encrypted Fernet ciphertext in the `integrations` table, never returned, never logged.
* Disconnect **deletes the local token only**. The OAuth API revoke call needs the app's credentials and is not used here. The grant therefore stays active at GitHub until the user removes JARVIS under **GitHub Settings > Applications**. The disconnect response says so (`provider_revoked: false`).

## Honest limits

* Not verified against live GitHub. Connect, the account read and the repository list are tested against a scripted transport only.
* The disconnect does not revoke the token at GitHub (see above).
* `github_list_repositories` reads `visibility=public` and the first page of 20 repositories, then returns up to the requested `max_results`. Private repositories are not listed.
* PKCE (`code_challenge_method=S256`) is sent on the authorization request. Not confirmed against live GitHub.
* The callback URL carries the `code` and `state` query parameters, which the Uvicorn access log records by default. See `docs/integrations/google_calendar.md` for the same caveat.
