# Phase 2: web app (contract for the verification agent)

Scope: the Integrations page (Google Calendar and GitHub: connect, test, disconnect, granted permissions), Memory search by meaning and the search-index build, the timezone field in Settings, Explore and the Automation tool list showing plugin tools, and the installable PWA. The user-facing behaviour is in `apps/web/README.md`. This change adds no backend route, field or environment variable. It only reads the contracts in `docs/phase2/integrations.md`, `memory.md` and `plugins.md`.

## Endpoints the web app calls

| Method and path | Used by | What the page reads or sends |
| --- | --- | --- |
| `GET /integrations` | Integrations | `items[]`: `status`, `implemented`, `requires_configuration`, `permissions`, `scopes_requested`, `account_label`, `last_checked_at`, `last_error_code`, `docs` |
| `POST /integrations/{id}/connect` | Connect, Reconnect | `{authorization_url}`. The page navigates only when the URL starts with `https:`. |
| `POST /integrations/{id}/test` | Test connection, Test again | `{ok, status, account_label, message}`. The message is shown as text. |
| `DELETE /integrations/{id}/connection` | Disconnect | `{status, provider_revoked, message}`. Sent only after the typed word `DISCONNECT`. |
| `GET /memories?semantic=true&limit&offset[&query][&category]` | Memory list | `search_mode`, `fallback_reason`. `semantic` is sent only when the switch is on. |
| `POST /memories/reindex` | Build search index | `{updated, remaining}`. Error `details`: `reason`, `updated`, `remaining`. |
| `GET /settings`, `PATCH /settings` | Settings, Memory switch | Timezone sends `{"timezone": "<IANA name>"}` or `{"timezone": null}` for UTC. Nothing else is sent by that control. |
| `GET /tools`, `GET /explore` | Automation tools, Explore | Plugin tools (`current_time`, `calculate`) are rendered with the `category` the API sends. No tool name is hardcoded. |

## Callback result

The provider returns to `/integrations?provider=<id>&status=connected` or `…&status=error&reason=<code>`. The page shows a banner and then removes the query string with `history.replaceState`. The provider name comes only from the catalogue, so an unknown `provider` value is shown as "The integration". Each reason code has fixed text. An unknown code gets "The connection did not finish. Try again."

## Manual checks

1. Without `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, Google Calendar shows "Requires configuration", lists both names, and has no Connect button.
2. With the GitHub client settings set, Connect shows "Opening GitHub sign-in" and the browser goes to the `https://github.com` address that the server returned. The browser does not build that address.
3. Open `/integrations?provider=github&status=error&reason=state_expired`. A red banner appears, and the address bar then shows `/integrations` only.
4. Disconnect GitHub. The dialog says the grant stays active at GitHub. The button stays disabled until `DISCONNECT` is typed exactly.
5. Memory: switch "Search by meaning" on. Without `GEMINI_EMBEDDING_MODEL`, the page shows `Fallback reason: no_embedding_model`. With it set and a saved Gemini key, "Build search index" shows the updated and remaining counts.
6. Settings, Automation: "Timezone for reminders and schedules" lists zones. Saving `Europe/London` sends only that key. Choosing the blank option sends `null`.
7. `GET /manifest.webmanifest` gives the JSON with icons `192x192` and `512x512`. `GET /icons/apple-touch-icon.png` is 180 by 180. `index.html` links the manifest, and no service worker is registered.
8. With `ENABLED_PLUGINS='["clock","calculator"]'`, Automation lists "Current time" and "Calculate", each with its category and risk.

## Limits

- The icons are placeholder art (an accent square with a J). Replace them with brand artwork at the same paths and sizes.
- There is no service worker and no offline support.
- The explore service currently files plugin and integration tools under "Automation". The page shows that category as sent.
- `apps/web/nginx.conf` is not changed. Check that `.webmanifest` is served as `application/manifest+json`.
- Google and GitHub were not called live. The web tests mock every request.
