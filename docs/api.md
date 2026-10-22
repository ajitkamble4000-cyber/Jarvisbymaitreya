# JARVIS AI — REST and SSE API contract

Base path: `/api/v1`. All bodies are JSON unless noted. Timestamps are RFC 3339 UTC (`2026-10-19T04:51:37Z`). IDs are UUID strings.

## Conventions

* **Authentication:** cookie `jarvis_session` (HttpOnly). Obtained from `/auth/setup` or `/auth/login`.
* **CSRF:** every non-GET request that carries the session cookie must send header `X-CSRF-Token` equal to the cookie `jarvis_csrf` (readable by JavaScript). `Origin` must be in `ALLOWED_ORIGINS` when present.
* **Errors:** always `{"error": {"code": "<stable_code>", "message": "<safe text>", "details": {}}, "request_id": "..."}`. Validation failures use `code = validation_error` and `details.fields[]`.
* **Lists:** `?limit=` (default 50, max 200) and `?offset=` (default 0). Response `{ "items": [...], "total": n }`.
* **Secrets** are never returned. Provider keys, device secrets and tokens are write-only.
* **Rate limits:** 429 with code `rate_limited`.

---

## Health and diagnostics

| Method | Path | Notes |
|---|---|---|
| GET | `/health/live` | `{status}` |
| GET | `/health` | `{status: ok\|degraded, version, database: ok\|error, background_workers, time}` |

## Auth (implemented)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/auth/status` | — | `{setup_required, authenticated, user?}` |
| POST | `/auth/setup` | `{email, password(≥12), display_name?}` | 201 `UserOut` (only while no user exists) |
| POST | `/auth/login` | `{email, password}` | `UserOut`; 401 `invalid_credentials` |
| POST | `/auth/logout` | — | 204 |
| GET | `/auth/me` | — | `UserOut` |
| GET | `/auth/sessions` | — | `[SessionOut]` (`current` flag) |
| DELETE | `/auth/sessions/{id}` | — | 204 |
| POST | `/auth/password` | `{current_password, new_password(≥12)}` | 204 (revokes other sessions) |

`UserOut`: `{id, email, display_name, role, created_at, last_login_at}`.

## Settings

`GET /settings` → `SettingsDoc`. `PATCH /settings` accepts a partial document (top-level sections with any subset of keys) and returns the full document. Unknown keys are rejected with 422.

```jsonc
{
  "ai": {
    "active_provider": "gemini",            // gemini | huggingface
    "fallback_provider": null,              // null | gemini | huggingface (must differ from active)
    "fallback_enabled": false,              // fallback only runs when this is true; always shown in the message
    "daily_call_limit": null,               // null = no limit; 1 to 10000 = provider calls per UTC day (chat and streamed replies). Refused turns make no call.
    "temperature": 0.7,                     // 0..2
    "max_output_tokens": 2048,              // 64..8192
    "system_instructions": "",              // ≤ 4000 chars
    "stream_responses": true,
    "include_memory": true
  },
  "voice": {
    "language": "en-US",
    "voice_name": null,
    "speaking_rate": 1.0,                   // 0.5..2.0
    "pitch": 1.0,                           // 0.5..2.0
    "captions_enabled": true,
    "auto_speak": true,
    "interruptions_enabled": true,
    "push_to_talk": true,
    "continuous_listening": false,          // only honoured when the browser actually starts recognition
    "wake_word_enabled": false,             // no wake-word engine is bundled; stored for the future
    "microphone_device_id": null,
    "audio_output_device_id": null
  },
  "appearance": {
    "theme": "dark",                        // dark | light | system | high-contrast
    "accent_color": "#22d3ee",              // #rrggbb
    "orb_style": "orb",                     // orb | ring | minimal
    "animation_intensity": "normal",        // off | low | normal | high
    "reduced_motion": "system",             // system | on | off
    "font_size": "medium",                  // small | medium | large
    "sidebar_hidden": [],                   // navigation ids hidden by the user
    "dashboard_widgets": {"status": true, "devices": true, "tasks": true, "activity": true, "schedule": true, "quick_actions": true}
  },
  "automation": {
    "max_concurrent_tasks": 3,              // 1..10
    "default_task_timeout_seconds": 120,    // 10..1800
    "disabled_tools": [],                   // tool names the user has switched off
    "emergency_stopped": false              // READ-ONLY here; set by POST /tasks/emergency-stop and /tasks/emergency-resume
  },
  "notifications": {
    "desktop_notifications": false,         // browser Notification API, requires permission
    "on_task_completed": true,
    "on_task_failed": true,
    "on_approval_required": true,
    "on_device_disconnected": true,
    "on_integration_error": true,
    "on_schedule_event": true,
    "on_security_event": true,
    "include_task_details": false,          // false = generic text in desktop notifications
    "quiet_hours_enabled": false,          // true needs both times below; browser pop-ups only, the inbox is unchanged
    "quiet_hours_start": null,             // "HH:MM" (00:00 to 23:59); null until set
    "quiet_hours_end": null                // "HH:MM", different from start; a start later than the end runs overnight
  },
  "memory": {"enabled": true, "retention_days": null, "auto_save_summaries": false},
  "privacy": {"store_conversations": true},
  "timezone": null                          // IANA name, e.g. "Asia/Kolkata"; null = UTC. Used for "HH:MM" reminders.
}
```

## AI providers

`ProviderOut`:

```jsonc
{
  "id": "gemini",                           // gemini | huggingface
  "display_name": "Google Gemini",
  "enabled": false,
  "configured": false,
  "credential_hint": null,                  // "…a1b2"
  "selected_model": null,                   // chosen from the live model list; never hardcoded
  "timeout_seconds": 30.0,                  // 1..120
  "max_retries": 2,                         // 0..5
  "status": "not_configured",               // not_configured | untested | connected | error
  "last_tested_at": null,
  "last_latency_ms": null,
  "last_error": null,                       // {code, message}
  "capabilities": {"streaming": true, "tool_calling": true, "multimodal_input": false, "usage_reporting": true, "model_listing": true}
}
```

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/providers` | — | `{items: [ProviderOut]}` |
| PUT | `/providers/{id}` | `{enabled?, selected_model?, timeout_seconds?, max_retries?}` | `ProviderOut` |
| PUT | `/providers/{id}/credential` | `{api_key}` | `ProviderOut` (key never echoed) |
| DELETE | `/providers/{id}/credential` | — | `ProviderOut` |
| POST | `/providers/{id}/test` | — | `{ok, latency_ms, message, error_code, account_label, provider}` |
| GET | `/providers/{id}/models` | — | `{items: [{id, display_name, supports_tools, supports_streaming, input_modalities}]}`; 409 `not_configured` |
| GET | `/providers/usage?days=30` | — | see below |

Connection tests use free endpoints only (Gemini model listing; Hugging Face `whoami`). They never generate content.

`GET /providers/usage` → `{period_days, generated_at, totals: {requests, successes, failures, input_tokens, output_tokens, total_tokens, tokens_estimated}, avg_latency_ms, by_provider: [...], by_model: [...], error_frequency: [{code, count}], estimated_cost_usd: null, cost_source: "unavailable", cost_note}`.

## Conversations and chat

`ConversationOut`: `{id, title, archived, provider_id, model, created_at, updated_at, last_message_at}`.
`MessageOut`: `{id, conversation_id, role: user|assistant|system|tool, content, status: complete|streaming|error|cancelled, provider_id, model, task_id, meta, created_at, edited_at}`.
`meta` may hold `{usage?, fallback_used?: {provider_id, reason}, tool_calls?: [...], latency_ms?}`.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/conversations?query=&archived=false&limit=&offset=` | — | `{items, total}` |
| POST | `/conversations` | `{title?}` | 201 `ConversationOut` |
| GET | `/conversations/{id}?limit=200` | — | `{conversation, messages: [MessageOut]}` (oldest first) |
| PATCH | `/conversations/{id}` | `{title?, archived?, pinned?}` | `ConversationOut` (`pinned` lists it first in its archived state) |
| DELETE | `/conversations/{id}` | — | 204 |
| POST | `/conversations/clear` | `{days: 1..3650, dry_run?: bool (default true)}` | `{matched, deleted, dry_run}`. Deletes the owner's conversations last updated more than `days` ago, never pinned ones. See [docs/phase4/features.md](phase4/features.md#clear-old-conversations). |
| GET | `/conversations/{id}/export?format=markdown\|json` | — | attachment (`Content-Disposition`), owner only, `no-store`. See [docs/phase3/settings-and-export.md](phase3/settings-and-export.md) |
| POST | `/conversations/{id}/messages` | `{content (≤ max_message_chars), provider_id?, model?}` | **SSE** |
| POST | `/conversations/{id}/messages/{message_id}/regenerate` | — | **SSE** (assistant message) |
| PATCH | `/conversations/{id}/messages/{message_id}` | `{content}` | `MessageOut` (user messages only; later messages are removed) |
| PUT | `/conversations/{id}/messages/{message_id}/feedback` | `{rating: "up" \| "down" \| null}` | `MessageOut`. Thumbs rating on a finished assistant reply, kept in `meta.feedback`. `null` clears it. A user or streaming message returns 409 `feedback_not_allowed`. |

Stopping generation: the client aborts the fetch; the server stores partial text with `status = cancelled`.

**SSE events** (`event:` name, JSON `data:`):

| Event | Data |
|---|---|
| `user_message` | `{message: MessageOut}` |
| `assistant_start` | `{message_id, provider_id, model}` |
| `text_delta` | `{message_id, text}` |
| `tool_proposed` | `{message_id, task: TaskOut, decision: "awaiting_approval" \| "queued" \| "needs_device_selection" \| "rejected", reason?, candidates?}` |
| `assistant_done` | `{message: MessageOut}` |
| `error` | `{code, message, message_id?}` (safe text, no secrets) |
| `done` | `{}` |

When a task finishes, the server appends a `tool` message with the result and an assistant message describing the real outcome, and publishes `chat.message` over the events stream.

## Devices

`DeviceOut`:

```jsonc
{
  "id": "…", "name": "Laptop", "device_type": "laptop", "os": "windows", "os_version": "10.0.22631",
  "agent_version": "1.0.0",
  "pairing_status": "paired",               // paired | revoked
  "connection_status": "online",            // online | offline | revoked
  "is_default": true,
  "capabilities": ["launch_app", "open_url", "system_info"],
  "permissions": {"launch_app": {"mode": "ask"}, "system_info": {"mode": "allow"}},
  "resources": {"apps": [{"id": "chrome", "label": "Google Chrome"}], "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}], "folders": [], "projects": [], "files": [], "checks": [{"id": "unit-tests", "label": "Unit tests"}]},
  "registered_at": "…", "paired_at": "…", "last_seen_at": "…", "last_connected_at": "…", "revoked_at": null
}
```

Resource entries never include local filesystem paths; the agent keeps those.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/devices` | — | `{items: [DeviceOut]}` |
| POST | `/devices/pairing-codes` | — | `{code: "KQ7M-2XBV", expires_at}` (single active code; shown once) |
| PATCH | `/devices/{id}` | `{name?, device_type?, is_default?, permissions?}` | `DeviceOut`. Permission modes: `ask`, `allow` (LOW-risk tools only), `deny` |
| POST | `/devices/{id}/ping` | — | `{online, latency_ms, message}` |
| POST | `/devices/{id}/revoke` | `{reason?}` | `DeviceOut` (closes the socket, invalidates the secret) |
| DELETE | `/devices/{id}` | — | 204 (revoked devices only) |

Agent-facing (no cookie; authenticated by the one-time code or the device secret):

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/pair` | `{code, device_name, device_type, os, os_version, agent_version, capabilities, resources}` → `{device_id, device_secret, websocket_path, heartbeat_interval_seconds, protocol_version}`. Code is single use, rate limited, and locked after `DEVICE_MAX_PAIRING_ATTEMPTS` failures. |
| WS | `/agent/ws` | `Authorization: Bearer <device_secret>`. Protocol in `docs/desktop-protocol.md`. |

## Tasks

`TaskOut`:

```jsonc
{
  "id": "…", "title": "Launch Google Chrome on Laptop", "description": "", "source": "chat",
  "tool_name": "launch_app", "arguments": {"app_id": "chrome"}, "risk_level": "medium",
  "status": "awaiting_approval",            // queued | running | awaiting_approval | completed | failed | cancelled
  "device": {"id": "…", "name": "Laptop", "connection_status": "online"},
  "conversation_id": "…", "required_permissions": ["launch_app"],
  "current_step": "Waiting for approval", "progress": null,   // progress only when measured: {value: 0..1, step}
  "result": null,                           // {summary, output} only after a succeeded result
  "error": null,                            // {code, message, details}
  "attempt": 0, "max_attempts": 1,
  "approval_expires_at": "…", "created_at": "…", "updated_at": "…", "started_at": null, "finished_at": null,
  "actions": {"approve": true, "deny": true, "cancel": false, "retry": false}
}
```

Error codes: `device_offline`, `device_not_found`, `needs_device_selection`, `device_revoked`, `permission_denied`, `capability_missing`, `resource_not_approved`, `invalid_arguments`, `approval_expired`, `approval_denied`, `cancelled_by_user`, `timeout`, `not_installed`, `execution_failed`, `unsupported_platform`, `agent_error`.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/tasks?status=&q=&limit=&offset=` | — | `{items, total, counts: {queued, running, awaiting_approval, completed, failed, cancelled}}` |
| GET | `/tasks/export.csv?status=&q=&limit=` (`limit` 1 to 2000, default 500) | — | CSV attachment `jarvis-tasks.csv`, newest first, `no-store`. Columns: `id, created_at, finished_at, source, title, tool_name, status, risk_level, error_code`. No arguments, results or events. Cells a spreadsheet would read as a formula get a leading `'`. |
| GET | `/tasks/{id}` | — | `{task: TaskOut, events: [{id, type, message, data, created_at}]}` |
| POST | `/tasks` | `{tool_name, arguments, device_id?, title?, description?}` | 201 `TaskOut` (policy applies) |
| POST | `/tasks/{id}/approve` | — | `TaskOut`. If the approval window has closed, the task is cancelled with `approval_expired` and the response is `409 approval_expired` |
| POST | `/tasks/{id}/deny` | `{reason?}` | `TaskOut` |
| POST | `/tasks/{id}/cancel` | — | `TaskOut` |
| POST | `/tasks/{id}/retry` | — | `TaskOut` (failed or cancelled tasks only) |

## Schedules

`ScheduleOut`: `{id, name, description, tool_name, arguments, device_id, schedule: {kind: once|daily|weekly, run_at, time_of_day, weekdays, timezone}, enabled, paused, next_run_at, last_run_at, last_status, consecutive_failures, retry: {max_attempts, backoff_seconds}, max_runtime_seconds, ended_at, state: scheduled|paused|disabled|completed|failing}`.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/schedules` | — | `{items}` ordered by `next_run_at` |
| POST | `/schedules` | `{name, description?, tool_name, arguments, device_id?, schedule, retry?, max_runtime_seconds?}` | 201 `ScheduleOut` |
| POST | `/schedules/preview` | `schedule` spec: `{kind, time_of_day?, weekdays?, run_at?, timezone?}` | `{runs: [up to 5 UTC instants]}`. Nothing is stored. See [docs/phase4/features.md](phase4/features.md#next-runs-preview). |
| PATCH | `/schedules/{id}` | `{name?, description?, enabled?, paused?, schedule?, arguments?, device_id?}` | `ScheduleOut` |
| DELETE | `/schedules/{id}` | — | 204 |
| GET | `/schedules/{id}/runs?limit=` | — | `{items: [{id, scheduled_for, status, attempt, task_id, started_at, finished_at, error}]}` |

Device tools in a schedule still create a task that waits for approval at each firing unless the device permission is `allow` and the tool is LOW risk.

## Memory

`MemoryOut`: `{id, category: fact|preference|project|task_context|conversation_summary, content, source: manual|conversation|system, created_at, updated_at, expires_at}`.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/memories?query=&category=&limit=&offset=` | — | `{items, total, enabled}` |
| POST | `/memories` | `{category, content (≤ 2000)}` | 201 `MemoryOut` (409 `memory_disabled` when off) |
| PATCH | `/memories/{id}` | `{content?, category?}` | `MemoryOut` |
| DELETE | `/memories/{id}` | — | 204 |
| DELETE | `/memories` | `{confirm: "DELETE ALL MEMORIES"}` | `{deleted}` |
| GET | `/memories/export` | — | JSON attachment `{exported_at, memories: [...]}` |
| GET | `/memories/{id}/history` | — | `{items: [{action, previous_content, new_content, created_at}]}` |

Secrets and credentials are rejected by the memory writer (pattern check) with 422 `sensitive_content`.

## Notifications

`NotificationOut`: `{id, kind, severity: info|success|warning|error, title, body, related_type, related_id, read, created_at}`.

| Method | Path | Response |
|---|---|---|
| GET | `/notifications?unread=true&limit=` | `{items, unread_count, hidden_by_preferences}`. Rows whose kind the notification settings turn off are hidden here, not deleted. See [docs/phase3/settings-and-export.md](phase3/settings-and-export.md) |
| POST | `/notifications/{id}/read` | `NotificationOut` |
| POST | `/notifications/read-all` | `{updated}` |
| DELETE | `/notifications/{id}` | 204 (dismiss) |

## Events (SSE)

`GET /events/stream` → `text/event-stream`. Events are signals to refetch REST data; they carry ids, not private content.

| Event | Data |
|---|---|
| `task.updated` | `{task_id, status}` |
| `approval.requested` | `{task_id, title}` |
| `device.updated` | `{device_id, connection_status}` |
| `schedule.updated` | `{schedule_id}` |
| `chat.message` | `{conversation_id, message_id}` |
| `notification.created` | `{kind, severity}` |
| `heartbeat` | `{time}` every `SSE_HEARTBEAT_SECONDS` |

## Tasks: emergency stop

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/tasks/emergency-stop` | `{confirm: "STOP"}` | `{stopped: true, cancelled: n, cancel_requested: m}`. `cancelled` counts queued and awaiting-approval tasks stopped now. `cancel_requested` counts running tasks that were asked to stop; they stay `running` until the device confirms. Blocks new automation until resumed |
| POST | `/tasks/emergency-resume` | — | `{stopped: false}` |

## Tools, explore and integrations

`GET /tools` → `{items: [{name, title, description, category, risk_level, target: server|device, requires_device, approval_required, status, example_commands, notes, parameters}]}`.

`GET /explore?category=&q=` → `{categories: [...], items: [{id, title, category, status: working|requires_configuration|experimental|unsupported, description, example_commands, requirements: [..], enabled_for_user}]}`. Discovering an item never authorises it.

`GET /integrations` → `{items: [{id, name, category, status: planned|requires_configuration|not_connected|connected|expired|disconnected|error, implemented, requires_configuration: [names], scopes_requested: [...], permissions: [granted scopes], docs}]}`. Google Calendar (`google_calendar`), GitHub (`github`), Gmail (`gmail`), Google Drive (`google_drive`) and Notion (`notion`) are implemented with OAuth 2.0 + PKCE (Gmail, Drive and Notion: see [docs/phase3/integrations.md](phase3/integrations.md)); their connect, callback, test and disconnect endpoints and the tools they add are described in [docs/phase2/integrations.md](phase2/integrations.md). Discord and Slack stay `planned`.

## Users (owner only)

Member accounts are managed by the owner. A member gets `403 forbidden` on every route, and a signed-out client gets `401`. Full rules, errors and audit events are in [docs/phase3/users.md](phase3/users.md).

| Method | Path | Body | Success |
| --- | --- | --- | --- |
| GET | `/users` | — | `{items: [AdminUser]}` |
| POST | `/users` | `{email, display_name, password (12 to 256), role: "member"}` | 201 `AdminUser` |
| PATCH | `/users/{id}` | `{is_active?, display_name?}` | `AdminUser` |
| POST | `/users/{id}/password` | `{new_password}` | 204 |

`AdminUser` is `{id, email, display_name, role, is_active, created_at, last_login_at}`. `password_hash` is never returned.

## Search

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/search?q=&limit=` | One search across conversations, memories and tasks: `{query, conversations, memories, tasks}`. `q` is 2 to 120 characters. Matches are literal and owner scoped. See [docs/phase4/features.md](phase4/features.md#global-search). |

## Account data and audit export

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/account/export` | The signed-in user's data as one JSON file (`jarvis-account-export.json`). Sections and the secrets left out: [docs/phase4/features.md](phase4/features.md#download-my-data). |
| GET | `/audit/export.csv?action=&outcome=&since=&until=&limit=` | The audit trail as CSV, without IP addresses or event data. |

## Audit, analytics, developer

`GET /metrics` (Prometheus text; `Authorization: Bearer <METRICS_TOKEN>`; 404 when `METRICS_TOKEN` is empty). Details in [docs/phase3/metrics.md](phase3/metrics.md).

`GET /audit?limit=&offset=&action=&outcome=&since=&until=` (`since` and `until` are inclusive instants; a zone-less time is UTC; `since` after `until` is `422 invalid_range`) → `{items: [{id, actor_type, actor_id, action, target_type, target_id, outcome, data, created_at}], total}`.

`GET /analytics/summary?days=30` → `{period_days, generated_at, sections: {...}}`. Every metric is `{value, unit, source: measured|estimated|unavailable, note?}`. Unavailable values are `null`, never zero.

`GET /analytics/calls-today` → `{calls, limit, resets_at}`. `calls` is the signed-in user's chat provider calls so far today (UTC), counted from usage records with operation `chat` or `stream`. `limit` is `ai.daily_call_limit` (null when unset). `resets_at` is the next midnight UTC. The daily limit check counts the same rows, so this is the number the limit is measured against.

`GET /developer/version`, `GET /developer/logs?level=&limit=` (redacted, owner only), `GET /developer/config-check` (`{checks: [{key, status: ok|warning|error, message}]}`, no values, owner only), `GET /developer/traces/{task_id}`.
