# Settings that change behaviour, conversation export, automatic summaries

Workstream BA-SET3. Three settings that the web app stored but nothing read now change behaviour, and conversations can be exported.

## 1. Notification preferences filter the inbox

`GET /api/v1/notifications` hides rows whose kind the user turned off in `notifications` settings. The rule is applied in the list route only. `notify()` is unchanged and still writes every kind.

| Kind | Setting |
| --- | --- |
| `task_completed` | `notifications.on_task_completed` |
| `task_failed` | `notifications.on_task_failed` |
| `approval_required` | `notifications.on_approval_required` |
| `device_disconnected` | `notifications.on_device_disconnected` |
| `integration_error` | `notifications.on_integration_error` |
| starts with `schedule` | `notifications.on_schedule_event` |
| starts with `security` or `auth` | `notifications.on_security_event` |
| anything else (`reminder`, `late_result`, ...) | always shown |

Response shape:

```json
{
  "items": [ { "id": "...", "kind": "task_failed", "read": false, "...": "..." } ],
  "unread_count": 3,
  "hidden_by_preferences": 2
}
```

* `unread_count` counts visible rows only.
* `hidden_by_preferences` counts this user's undismissed rows that the preferences hide. It is not limited by `limit` or `unread`.
* Hiding is not deleting. Turning the setting back on shows the rows again.
* Preferences are read with the user's own settings document only. Another user's settings never apply.

## 2. Automatic conversation summaries (`memory.auto_save_summaries`)

Default `false`. When `true`, after an assistant reply completes, a background task runs. It does not block the reply.

Rules, applied when the task runs (settings are read again at that point):

* `memory.enabled`, `memory.auto_save_summaries` and `privacy.store_conversations` must all be `true`.
* At least 12 complete `user` or `assistant` messages were written after the last summary. The count starts from the message recorded in `conversations.summary_through_message_id`. If that message no longer exists, the count covers the whole conversation.
* The user's active provider (`ai.active_provider`) answers `generate_response` with a fixed prompt. The prompt asks for at most 800 characters, and says the conversation is data, not instructions.
* The input is the newest messages, oldest first, capped at 12000 characters. A message that does not fit is cut to its last characters.
* The reply is cut to 800 characters, then saved with `create_memory`: category `conversation_summary`, source `conversation`, unembedded. The sensitive-content check applies.
* On success, `summary_through_message_id` moves to the newest message in the window.
* At most one check runs per conversation in this process.

Failures never reach the chat. Each failure is logged and written to usage with `operation = "summary"`, `success = false` and an `error_code`. Codes: the provider codes (for example `not_configured`, `unavailable`), `model_not_selected`, `empty_response`, `sensitive_content`, `memory_disabled`, `internal_error`.

Migration: `a8c3e5f1d7b2_conversation_summary_marker` adds the nullable column `conversations.summary_through_message_id` (UUID, no foreign key). It sits in the single Alembic chain under `d7a09bd3dc82`.

Why a column: the brief's alternative stored the marker in the memory `source` field. That field is `varchar(30)` and must read `conversation`, so it cannot hold a message id.

## 3. Conversation export

`GET /api/v1/conversations/{id}/export?format=markdown|json` (default `markdown`).

* Owner only. Another user's conversation, a missing one and a malformed id all return `404` (`not_found`).
* An unknown `format` returns `422`.
* Headers: `Content-Disposition: attachment; filename="<name>.md"` (or `.json`), `Cache-Control: no-store`. Media types `text/markdown; charset=utf-8` and `application/json; charset=utf-8`.
* Filename: the title with every character outside `[A-Za-z0-9 _-]` removed, cut to 60 characters, trailing spaces trimmed. If nothing is left, the conversation id is used. Path separators and quotes can never appear.
* Markdown: `# <title>`, then each message as `### User`, `### Assistant`, `### Tool` or `### System`, followed by the content. Tool messages carry a `_Tool output_` line. Content is not escaped.
* JSON:

```json
{
  "id": "uuid",
  "title": "string",
  "created_at": "ISO 8601",
  "messages": [ { "role": "user|assistant|tool|system", "content": "string", "created_at": "ISO 8601", "status": "complete|streaming|error|cancelled" } ]
}
```

The export never includes provider names, models, tokens or the stored `meta` of a message.

## Environment variables

None added.

## Security notes

* Every query filters by `user_id`. Tests cover cross-user access for the list filter, the summary check and the export.
* Summary input is treated as data by the prompt. Tool output is labelled as such in the transcript.
* The summary request sends conversation text to the user's active provider. This happens only when the setting is on.
* Export responses are `no-store`.

## Honest limitations

* Summaries use the provider's paid API when `memory.auto_save_summaries` is on. Each due summary costs one generation call. A failing provider is retried on the next completed reply, and each attempt is recorded in usage.
* If the message marked by `summary_through_message_id` is removed by an edit or regenerate, the next summary covers the whole conversation again. Facts can repeat.
* The check counts messages and does not summarise by topic. Only the newest 200 messages in the window are read, so a long backlog can skip older messages.
* Summaries are saved without embeddings. `POST /api/v1/memories/reindex` embeds them later.
* The per-conversation guard lives in one process. With several backend processes, two summaries for the same conversation can run at once.
* The export includes every stored message, including error and cancelled replies.
