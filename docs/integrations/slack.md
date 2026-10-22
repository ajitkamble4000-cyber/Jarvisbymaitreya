# Slack integration

Status: **planned**. Not implemented. Nothing can be connected yet.

## What it would do

List the channels in a connected workspace so JARVIS can refer to them, and in a later version summarise approved channels. The first version would not post messages.

## Required setup (future)

* A Slack app with OAuth configured. Environment variables: `SLACK_OAUTH_CLIENT_ID`, `SLACK_OAUTH_CLIENT_SECRET`. These names are proposed and not yet used by any code.
* The redirect URI must match `PUBLIC_BASE_URL`.

## Scopes requested

* `channels:read`

Reading channel history would need a further scope. That decision is deferred until the feature is designed.

## Honest limits

* The OAuth flow, token storage and Slack API calls are not implemented.
* The `/integrations` endpoint reports `implemented: false` and `status: planned`.
