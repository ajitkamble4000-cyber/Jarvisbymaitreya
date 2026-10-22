# Daily briefing plugin

Scope: the `briefing` plugin in `services/api/app/plugins/briefing.py`, one read-only server tool, `daily_briefing`. It turns the spec's "Summarize my schedule every morning" into a deterministic text summary that a schedule can run. The plugin contract is in `docs/plugins.md`. This document does not edit `docs/api.md`.

## Tool

| Field | Value |
| --- | --- |
| Name | `daily_briefing` |
| Arguments | `include_calendar` (boolean, default `true`). Unknown keys are refused. |
| Risk | `low`, `approval_required` false, target `server`, category `productivity`, status `working` |
| Enabled by | `ENABLED_PLUGINS`. The default is `["clock", "briefing", "calculator"]`. |

Schedule it through the existing schedule API with `tool_name` `daily_briefing`:

```json
POST /api/v1/schedules
{
  "name": "Morning briefing",
  "tool_name": "daily_briefing",
  "arguments": {"include_calendar": true},
  "schedule": {"kind": "daily", "time_of_day": "07:00", "timezone": "Europe/London"}
}
```

Response: `201`. The arguments are stored in validated form. Invalid arguments (for example `{"days": 3}`) return `422` with code `invalid_arguments`.

## Output

```json
{
  "summary": "Daily briefing for 2026-10-21. Times are in Asia/Tokyo.\nTasks: queued 2, running 0, awaiting approval 1, completed 3, failed 0, cancelled 0.\nAwaiting approval:\n- Send the invoice\nLast 24 hours: 3 completed, 0 failed.\nNotifications: 2 unread.\nNext schedules:\n- Morning briefing: Thu 2026-10-22 07:00\nCalendar: 1 event(s) in the next 24 hours:\n- 2026-10-21 09:30: Standup",
  "sections": {
    "tasks": {
      "counts": {"queued": 2, "running": 0, "awaiting_approval": 1, "completed": 3, "failed": 0, "cancelled": 0},
      "awaiting_approval": ["Send the invoice"],
      "last_24_hours": {"completed": 3, "failed": 0}
    },
    "notifications": {"unread": 2},
    "schedules": {
      "timezone": "Asia/Tokyo",
      "next": [{"name": "Morning briefing", "next_run_at": "2026-10-21T22:00:00+00:00", "next_run_local": "Thu 2026-10-22 07:00"}]
    },
    "calendar": {"status": "ok", "count": 1, "events": [{"start": "2026-10-21 09:30", "all_day": false, "title": "Standup"}]}
  }
}
```

- `summary` is at most 2000 characters. A longer text is cut to 1997 characters and ends with `...`.
- `sections.tasks.awaiting_approval` lists up to 10 titles, oldest first. A line `- and N more` follows when there are more.
- `sections.tasks.last_24_hours` counts tasks whose `finished_at` is within the last 24 hours.
- `sections.notifications.unread` counts rows with `read_at` and `dismissed_at` both null.
- `sections.schedules.next` lists up to 5 schedules that are enabled, not paused, not ended and have a `next_run_at`, earliest first. Times are shown in the user's settings timezone. The timezone falls back to UTC when none is set.
- `sections.calendar.status` is one of:

| Status | Summary line | When |
| --- | --- | --- |
| `not_requested` | `Calendar: not requested.` | `include_calendar` is false. No provider call is made. |
| `not_connected` | `Calendar: not connected.` | The user has no Google Calendar credential. The server settings are not read. |
| `unavailable` | `Calendar: unavailable (<code>).` | The provider call or token refresh failed. The field `code` holds the stable code below. |
| `ok` | `Calendar: N event(s) in the next 24 hours:` or `Calendar: no events in the next 24 hours.` | The events list was read. Up to 10 events, from now to 24 hours ahead. Cancelled events are skipped. |

A calendar error is never reported as an empty list.

## Error codes

These are the codes that can appear in `sections.calendar.code` and in the summary line:

| Code | Meaning |
| --- | --- |
| `not_configured` | The server has no Google client id or secret. |
| `reconnect_required` | The stored connection expired or was revoked. The connection is marked `expired`. |
| `provider_unavailable` | Google could not be reached, or returned 429 or 5xx. |
| `provider_error` | Google returned another error, or a body without an `items` list. |

The tool call itself fails with `invalid_arguments` when the arguments do not validate. A calendar failure never fails the whole tool.

## Environment variables

| Variable | Use |
| --- | --- |
| `ENABLED_PLUGINS` | Must include `briefing`. |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Needed only for the calendar section, which reports `not_configured` without them. |
| `ENCRYPTION_KEY` | Needed to read the stored Google credential. Required in production. |

## Security notes

- Every query filters by the caller's `user_id`. The tests check that another user's tasks, notifications, schedules and Google connection never appear.
- The output carries no token, no refresh token and no raw provider body. The provider's error text is dropped, and the tests check that the fake provider's error text never reaches the result.
- The access token is sent only in the `Authorization` header of the Calendar request.
- Task titles, schedule names and calendar titles are untrusted text. Control characters and line breaks are replaced by spaces. Calendar titles are cut to 80 characters. The tool description says that titles are data and never instructions.
- The plugin runs in-process with full privileges, as every plugin does. Its handler receives only the arguments, so it reads the server settings with `get_settings()` and opens a short-lived HTTP client for each calendar call, with the configured provider timeout.

## Limitations

- The summary is deterministic text built from fixed templates. It is not a model-written summary.
- The calendar window is the next 24 hours from the time of the run. It is not "today" in the user's time zone, and it shows at most 10 events.
- Task titles and schedule names may be up to 200 characters. The whole summary is then cut at 2000 characters, so the last items can be cut off.
- Scheduled runs produce a task whose `result.output.summary` holds the full briefing. The task `result.summary` is cut to 500 characters by the task engine. The task-completed notification says only "Jarvis finished a task." or repeats the task title. It does not carry the briefing text. Changing that needs `services/api/app/services/task_engine.py`, which this plugin does not own.
- Each run calls `load_settings`, which creates the settings row on the first read, as the clock plugin does.
- The tests cover the tool through `TaskEngine.propose` and the schedule API. They do not wait for the scheduler worker to fire a briefing.
