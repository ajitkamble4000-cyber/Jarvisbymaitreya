# Phase 4: features added after the phase 3 verification

These features were added on top of the phase 3 tree. Each one has tests in the repository. The verification level
for each is in the table below; the rules are described in the section for each feature.

| Feature | Backend | Web | Verification |
|---|---|---|---|
| Task list export as CSV | `GET /api/v1/tasks/export.csv` | "Export CSV" on the Tasks page | Unit tests (cell rules, columns, line endings), integration tests (scoping, filters, headers, route order). Web: download helper tests with a mocked fetch. |
| Quiet hours for browser pop-ups | `notifications.quiet_hours_*` settings, validated | Quiet hours card in Settings, Notifications | Integration tests for the settings rules; web tests for the window rules, the alert gate and the form. Not run in a real browser. |
| Command palette (Ctrl+K, Cmd+K) | none (navigation only) | Palette in the app shell | Web tests for the filter, the keyboard rules and the component. |
| Thumbs rating on assistant replies | `PUT .../messages/{id}/feedback` | Two buttons under finished replies | Integration tests (owner scope, rules, validation); web tests for the rules and the buttons. |
| Pinned conversations | `conversations.pinned` (migration `e1f4b8c2a9d3`), PATCH, list order | Pin toggle in the conversation list | Integration tests (default, round trip, order, strict boolean); migration test (metadata matches, downgrade clean); web tests. |
| Audit trail as CSV | `GET /api/v1/audit/export.csv` | none (use the API or a script) | Integration tests: fixed columns, no IP address or event data, filters match the list. |
| Download my data | `GET /api/v1/account/export` | "Download my data" in Settings, Privacy | Integration tests, including a scan of every object key for secrets and a planted provider key that must not appear. Member isolation tested. Web tests with a mocked fetch. |
| Next runs preview for schedules | `POST /api/v1/schedules/preview` (nothing saved) | "Next runs" under the timezone field on the schedule form | Integration tests (daily, weekly, a spring-forward gap, a past one-time spec, auth, bad spec); web tests for the spec builder and the component states. |
| Clear old conversations | `POST /api/v1/conversations/clear` (`dry_run` first) | Card on Settings, Privacy: count, then delete after typing `DELETE` | Integration tests: count without deleting, deletion with messages, pinned kept, archived included, validation, audit without titles, member scope. Web tests: count, the typed confirmation, the request bodies, the empty case. |
| Activity CSV button | `GET /api/v1/audit/export.csv` (existing) | "Export CSV" on the Activity page, with the page's filters | Web tests: the request route and the outcome filter. |
| Run again for finished tasks | `POST /api/v1/tasks` (existing) | "Run again" on a completed, failed or cancelled task | Web tests: which statuses offer it, the body sent (tool, arguments, device, title), hidden while waiting for approval. Policy applies to the new task. |
| Global search | `GET /api/v1/search?q=&limit=` | Results inside the command palette ("Found" group) | Integration tests: one call across conversations, memories and tasks, title versus message matches, literal wildcards, length limits, expiry, member isolation. Web tests: hit-to-command mapping, typed search opening a conversation. |
| High-contrast theme | `appearance.theme` accepts `high-contrast` | Option in Settings, Appearance; own token block in the stylesheet | Settings schema tests; web tests that the value is kept and gets its own `data-theme`, and that the token block exists. Contrast ratios computed: at least 13:1 for text on both surfaces. |
| Keyboard shortcuts in Help | none | Card on Help listing the shortcuts the app really has | Covered by the palette tests for the bindings it lists. |
| First-run checklist on Overview | none (derived from data the page already loads) | `OnboardingCard` on Overview, hidden for each browser when dismissed | Web tests: each step's rule, the links, dismissal in localStorage (and when storage fails), hidden when complete. |
| Daily AI call limit | `ai.daily_call_limit`, enforced before any provider call | Field in Settings, AI | Integration tests with the chat fixture: refusal makes no provider call, one warning per day, earlier days and other operations do not count, strict integer validation. |
| Find in this conversation | none (client-side, over the loaded messages) | Find box above the messages, with a "N of M messages match" line | Web tests: blank query keeps everything, matching ignores case, the count line and the no-match message. Searches the most recent 200 loaded messages only. |
| Read reply aloud | none (browser speech output; reads `GET /api/v1/settings` for the voice) | Button under each finished assistant reply, shown only where the browser can speak | Web tests: hidden without speech support, speaks and then stops, a button that unmounts does not silence a reply another button is reading. Not heard in a real browser. |
| Calls today on the AI page | `GET /api/v1/analytics/calls-today` | Line under the daily call limit on Settings, AI | Integration tests: chat and stream calls only, today only, per user, the limit echoed, sign-in required. Web tests: the sentence in each state, and the line left out when the count cannot be read. |
| Recent conversations in the palette | `GET /api/v1/conversations` (existing) | First five conversations under Recent when the palette opens | Web tests: the five first, the order kept, a click runs it, the list goes once something is typed. |
| Date range on Activity | `since` and `until` on `GET /api/v1/audit` and `/audit/export.csv` | From date and To date fields on the Activity page | Integration tests: both ends inclusive, the CSV covers the same range, a zone-less time read as UTC, a backwards range and a bad time refused. Web tests: whole local days, the backwards message and that it is not sent, the export carries the range. |
| Unsent chat drafts | none (browser localStorage) | Message box keeps its text per conversation until sent; cleared on sign out | Web tests: each conversation keeps its own text, sending clears it, inserted text wins, blank and over-long text is not kept, blocked storage is harmless, sign out removes every draft. Not checked in a real browser. |
| Follow a streaming reply, with Jump to latest | none (scroll position in the page) | "Jump to latest" appears when the reader has scrolled away from the end of the conversation | Web tests: a conversation opens at its end, the button appears after scrolling away, it brings the reader back and hides, following resumes when the end is on screen again. Not checked in a real browser. |

## Task list export

`GET /api/v1/tasks/export.csv` takes the same `status` and `q` filters as the list, and `limit` (1 to 2000, default 500).
The rows are the owner's, newest first. The file is `text/csv; charset=utf-8` with `Content-Disposition: attachment;
filename="jarvis-tasks.csv"` and `Cache-Control: no-store`.

Columns: `id`, `created_at`, `finished_at`, `source`, `title`, `tool_name`, `status`, `risk_level`, `error_code`.
The error code comes from the error envelope. Arguments, results, device output and the event timeline are never
included, because they can hold file paths, URLs and command text.

A cell that a spreadsheet program would read as a formula (it starts with `=`, `+`, `-`, `@`, a tab or a carriage
return) gets a leading apostrophe. Line endings are CRLF and fields are quoted as RFC 4180 requires.

The route is declared before `/tasks/{task_id}`, so `export.csv` is never read as a task id.

## Quiet hours

Quiet hours pause browser pop-ups only. The inbox, the API and the task engine are not affected.

Settings (in `notifications`):

| Key | Type | Rule |
|---|---|---|
| `quiet_hours_enabled` | boolean | Default `false`. Must be a real boolean. |
| `quiet_hours_start` | `"HH:MM"` or null | `00:00` to `23:59`. Required when quiet hours are on. |
| `quiet_hours_end` | `"HH:MM"` or null | Same format. Required when quiet hours are on. Must differ from the start. |

The rules are checked on the merged document, so a patch that turns quiet hours on without the times is refused with
422. A window whose start is later than its end runs overnight (22:30 to 07:00). The start is inclusive and the end is
exclusive: at the end minute pop-ups are allowed again.

The times are read in the reminder time zone (`timezone` in settings). When no reminder zone is set, this browser's own
zone is used. An unknown zone name falls back to the browser's zone rather than failing.

## Command palette

Ctrl+K (Windows, Linux) or Cmd+K (macOS) opens the palette from any page. Typing filters the list. Every word typed must
match a label, a keyword or a group, ignoring case. Arrow keys move the highlight and wrap at both ends. Enter runs the
highlighted command. Escape closes the palette.

Commands are routes that already exist: every top-level page (including pages hidden from the sidebar) and a few
actions, such as "New chat", "New task", "Pair a desktop agent", "Review approvals", and shortcuts to the voice, notification,
AI, appearance and privacy settings ("daily limit" finds AI settings, "high contrast" finds appearance, "clear conversations"
finds privacy). A command never performs a server action itself.

Before anything is typed, the palette lists the first five conversations of the list under **Recent**. Pinned conversations come first in that list. The list is the one the sidebar already loads, so opening the palette usually costs no extra request. Each option is named with its group, so a screen reader says "Overview, Go to" rather than running the words together.

## Thumbs rating on assistant replies

`PUT /api/v1/conversations/{id}/messages/{message_id}/feedback` with `{"rating": "up" | "down" | null}` stores the rating
in the message's `meta.feedback` as `{"rating", "given_at"}`. The other keys in `meta` are kept. `null` removes the
rating. Pressing the rating already given clears it on the web.

Rules: the owner only (another user gets 404), a finished assistant reply only (a user message or a streaming reply gets
409 `feedback_not_allowed`), and a strict body (other values, unknown keys or a missing key get 422).

The export does not include `meta`, so ratings are not written into exported files.

## Pinned conversations

`conversations.pinned` is a boolean that defaults to false. Migration `e1f4b8c2a9d3` adds it with a server default, so existing
conversations become unpinned. `PATCH /conversations/{id}` accepts `pinned` as a strict boolean, and a patch without the key keeps
the current value. The list orders pinned conversations first within the archived or active set, then by last update.

## Audit trail as CSV

`GET /api/v1/audit/export.csv` takes the same `action` prefix and `outcome` filters as the list, plus `limit` (1 to 2000,
default 500). Columns: `created_at, actor_type, actor_id, action, target_type, target_id, outcome`. The IP address and the
event data are left out. The file is `jarvis-audit.csv`, `no-store`, and every cell passes the same formula rules as the task export.

Both the list and the export take `since` and `until`, instants that include both ends. A time without a zone is read as UTC. A `since` after `until` is refused with `422` and the code `invalid_range`. On the Activity page the two date fields cover whole days in this browser's time zone, from midnight on the first day to the last moment of the last day. A backwards range is reported under the To date field. While it is backwards, neither date is sent, so the list shows unfiltered results until the dates are fixed.

## Download my data

`GET /api/v1/account/export` returns one JSON file, `jarvis-account-export.json`, with `format_version`, `exported_at`,
the list of `sections`, and one key per section: `profile`, `settings`, `conversations` (with messages), `memories`,
`tasks` (with arguments and results), `devices` (labels only), `integrations` (status and granted scopes), `providers`
(provider id, enabled, selected model), `schedules`, `notifications` and `audit`.

Each section names its fields in code. Password hashes, session tokens, device secrets, provider keys and their hints,
integration credentials and integration error messages are never selected, so a column added later is not exported
unless someone adds it to that list. The export is written to the audit trail as `account.exported` without an IP address.
It is scoped to the signed-in user.

Keep the file private: it holds conversations and task arguments.

## Daily AI call limit

`ai.daily_call_limit` is null by default (no limit). A whole number from 1 to 10000 limits the provider calls per UTC day.
Calls are counted from `usage_records` for chat and streamed replies. When today's count reaches the limit, the next turn
fails before any provider is called with the error code `daily_call_limit_reached`. The refused turn is not counted, so a
refusal does not use up the next day. The first refusal of a day also creates one notification of kind `usage_limit`,
severity `warning`.

The limit counts calls, not tokens. The usage table does not record token counts, so a token budget is not offered.
The limit does not stop a fallback: a refused turn has no retryable failure, so no fallback provider is tried.

The AI page shows the day's count under the field: "Used today: 3 of 10 AI calls. The count resets at midnight UTC." It reads `GET /api/v1/analytics/calls-today`, which counts with the same query as the limit check, so the two cannot disagree. Without a limit the sentence says no limit is set. When the count has reached the limit, it says so and that replies resume after midnight UTC. If the count cannot be read, the line is left out and the form still works.

## Next runs preview

`POST /api/v1/schedules/preview` takes a schedule spec (the same shape as the `schedule` field of a create) and returns
`{"runs": [ISO 8601 UTC instants]}`, at most five, in order. It uses the scheduler's own timing functions, so daylight-saving
changes and skipped local times give the same instants the scheduler would use. A one-time spec in the past has no runs. Nothing
is stored, and the request needs a signed-in session.

On the schedule form, the preview waits 400 ms after the last change, then requests the runs for the timing alone. It does not
need a name or a tool, so it can show runs while those are still blank. The times are shown in this browser's zone; the schedule
itself runs in the zone it was saved with.

## First-run checklist

Overview shows five steps until all are done: an AI provider key, a model, a paired desktop agent, a time zone for reminders,
and a first message. Each step comes from data Overview already loads, so a step is shown as done only when the server reports it.
"Hide this checklist" stores a flag in this browser's localStorage. If storage is blocked, the checklist simply shows again.

## High-contrast theme

`appearance.theme` accepts `high-contrast` in addition to `dark`, `light` and `system`. It applies `data-theme="high-contrast"`,
which has its own token block in `styles/index.css`: pure black surfaces, white text, a light border colour and brighter
accents. The contrast ratios were computed from the hex values: text reaches at least 13:1 on the canvas and on raised
panels. The theme does not change the layout, the motion setting or the font size.

## Keyboard shortcuts

Help lists the shortcuts the app really has: Ctrl+K or Cmd+K for the palette, the arrow keys and Enter inside the palette,
Enter and Shift+Enter in the chat box, and Escape for dialogs and the palette. Nothing is listed that the app does not do.

## Global search

`GET /api/v1/search?q=<text>&limit=<1..10>` (default 5, query 2 to 120 characters) searches the signed-in user's conversations
(by title, or by the text of any message in them), memories (by content, not past their expiry) and tasks (by title or tool name).
The response has `conversations`, `memories` and `tasks`, each capped by `limit`. A conversation says whether it matched by
`title` or by `message`. A memory returns an excerpt of 140 characters.

`%` and `_` are matched as ordinary characters, so typing them does not match everything. Every query is scoped to the owner.

In the command palette, text of two or more characters also searches (after a short pause). Hits appear under "Found". A conversation
or task opens its page, and a memory opens the Memory page.

## Activity export and run again

The Activity page's **Export CSV** downloads `jarvis-audit.csv` with the action prefix and outcome currently on screen.

On a finished task (completed, failed or cancelled), **Run again** creates a new task with the same tool, arguments, device and
title, then opens it. The new task goes through the normal policy: a tool that needs approval asks again. A running or waiting task
has no Run again, so the same action is never queued twice.

## Clear old conversations

`POST /api/v1/conversations/clear` with `{"days": 1..3650, "dry_run": true | false}` (`dry_run` defaults to true) counts or deletes
the owner's conversations last updated more than `days` ago. Pinned conversations are never deleted. Archived ones are, because
they are old too. Messages are removed with their conversation, and tasks keep their history with the link cleared. The response is
`{"matched", "deleted", "dry_run"}`. A real run writes one audit event, `conversations.cleared`, with the number of days and the
count. It does not write titles.

The Settings, Privacy card counts first. The delete button stays disabled until `DELETE` is typed, and the delete runs again on the
server, so the count is never trusted to be current. This cannot be undone.

## Find in this conversation

The find box above the messages filters the open conversation. It matches the text of each message, ignoring case, and it does not
call the server. A blank box shows every message. The status line says how many messages match, and when none do, the message list
says so. Streaming replies are included while they arrive.

The box searches the most recent 200 messages that the page loads, which is the same window the conversation shows. Older messages
are not searched. Use global search for everything else.

## Read reply aloud

A finished assistant reply has a **Read reply aloud** button when the browser reports speech output. It speaks the reply text with
the browser's own voices, using the language, voice, speed and pitch saved on the Voice page. While speech plays, the button reads **Stop reading** and is marked as pressed. The state follows the
engine's end and error events, so it returns to the play button only when speech has really ended.

Only one reply speaks at a time: starting another cancels the first. A button that leaves the screen, for example when the find box
hides its message, stops speech only if it was the one speaking. JARVIS does not send the reply anywhere for this. The browser's
speech engine does the work, and some browsers use their own online voices.

## Unsent chat drafts

What you type in the message box is kept in this browser until you send it. Each conversation has its own draft, and the new-chat box has one too, so moving between chats no longer carries text from one into another. Drafts are stored in localStorage under keys that start with `jarvis.chat-draft.`. They never go to the server.

Sending clears the draft. Blank text removes it, and text longer than 20,000 characters is not kept. Text inserted from a link or the voice page shows in the box in place of the stored draft, and is stored once you edit it. Signing out removes every chat draft from this browser, so a shared computer keeps no unsent text. If storage is blocked or full, the box works as before and the draft is simply not kept.

## Following a streaming reply

A reply that streams in keeps the end of the conversation on screen, as before. The difference is what happens when the reader scrolls up to read earlier text: the page now stays where the reader put it. A **Jump to latest** button appears at the end of the messages, and pressing it returns to the end and resumes following.

Only the reader's own scrolling decides whether the end is followed. Growing content does not, so a reply that streams in cannot pull the reader away. Opening another conversation always starts at its end.
