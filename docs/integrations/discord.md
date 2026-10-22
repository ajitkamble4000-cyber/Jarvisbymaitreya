# Discord integration

Status: **planned**. Not implemented. Nothing can be connected yet.

## What it would do

Identify the connected Discord account and, in a later version, show recent messages from channels the user approves. The first version would not post messages.

## Required setup (future)

* A Discord application with OAuth2 enabled. Environment variables: `DISCORD_OAUTH_CLIENT_ID`, `DISCORD_OAUTH_CLIENT_SECRET`. These names are proposed and not yet used by any code.
* The redirect URI must match `PUBLIC_BASE_URL`.

## Scopes requested

* `identify`

## Honest limits

* The OAuth flow, token storage and Discord API calls are not implemented.
* The `/integrations` endpoint reports `implemented: false` and `status: planned`.
