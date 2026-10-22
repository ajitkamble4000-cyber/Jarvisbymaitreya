# Phase 3: web app

Workstream WEB3. The web app now uses the phase 3 backend features and fixes the accessibility findings from the TEST3 run. This file lists what the screens do and what they still do not.

## Screens and what changed

| Route | Change |
| --- | --- |
| `/chat/:id?` | **Export** buttons (Markdown, JSON) in the chat header, for a saved conversation. `?prompt=` fills the composer and never sends. |
| `/voice` | Push to talk, interruptions and continuous listening read the saved `voice.*` settings. Captions and spoken replies were already saved settings. |
| `/settings` | New **Users** tab, owner only. Voice rows that were "Not applied yet" are now switches. Task timeout and summary toggle are real controls. Desktop alert permission is shown in words. |
| `/notifications` | "N notifications are hidden by your settings" with a link to Settings, Notifications, when the API reports `hidden_by_preferences` above zero. |
| `/memory` | Memories whose `source` is `conversation` show a **Summary** badge. |
| `/integrations` | Gmail, Google Drive and Notion use the same card, banner, test, disconnect and permission rendering as the other providers. No provider has its own code path any more. The disconnect dialog's text is the same for every provider; whether the provider also revoked access is taken from the server's message after disconnecting. |
| `/devices` | Approved **checks** are listed under the resources, with the note that every check needs approval, even when the device is set to allow. Only labels are shown. |
| `/help` | New section "Open the chat with a prompt": link format, the 2000-character cap, and an iPhone Shortcut example. |

## Settings the web app now applies

| Setting | Where it takes effect |
| --- | --- |
| `voice.push_to_talk` | Voice page. `true` is hold to talk. `false` is press once to start and again to stop. The mode switch on the Voice page saves this setting. |
| `voice.interruptions_enabled` | Voice page. When `true`, the viewer starting to speak stops a spoken reply. |
| `voice.continuous_listening` | Voice page. Recognition restarts after each utterance while the page is open and the microphone is on. The status says "Listening continuously" only while the browser reports that recognition is running. |
| `voice.captions_enabled`, `voice.auto_speak` | Voice page, as before. |
| `notifications.desktop_notifications` | Browser alerts (below). |
| `notifications.on_approval_required`, `on_task_completed`, `on_task_failed` | Browser alerts. |
| `notifications.include_task_details` | Browser alerts. Off by default, so the text is generic. |
| `notifications.on_integration_error`, `on_security_event` | The inbox filter in the API. The web app shows the switches; it does not filter anything itself. |
| `automation.default_task_timeout_seconds` | Number field, 10 to 1800. Saved with only the changed keys. |
| `memory.auto_save_summaries` | Switch. The text says it uses the active provider's paid API. |

## Browser alerts

Alerts are made by `src/services/browserNotifications.ts`.

* No alert is made while the tab is visible (`document.hidden` is false). Visible tabs show the existing approval toast only.
* Alerts are made for `approval.requested`, and for `task.updated` with status `completed` or `failed`. Each one needs its setting on.
* Without task details the title is "Approval needed", "Task completed" or "Task failed", and the body is generic. With `include_task_details` on, the body uses the approval title, or a task title the page already has cached. A task title is never fetched only for the alert.
* Permission is asked only from the switch's change handler, so the request follows a click. The permission state is shown as text: granted, denied, not decided, or unsupported.
* An alert is shown only when the permission is `granted`. Each task has one tag, so a repeat replaces the earlier alert.
* The browser's `Notification` constructor is called only in `showBrowserAlert`, and errors are caught.

**Not done:** no service worker and no Web Push. Alerts exist only while the page is open in a background tab. Closed tabs get nothing.

## Export

`src/services/exportDownload.ts` fetches `GET /api/v1/conversations/{id}/export?format=` with the session cookie. The file is read as a Blob and saved through a temporary object URL that is revoked after one second. The file name comes from `Content-Disposition`. Only letters, digits, dot, space, underscore and dash survive. The rest of the name is never placed into HTML.

## Deep links

`/chat?prompt=<text>` fills the composer once for each new link. Text is capped at 2000 characters, and the chat says when it was cut. The link never sends the text. The user reads it and presses Enter.

```
https://jarvis.example.com/chat?prompt=Summarise%20my%20unread%20notifications
```

## Accessibility

`tests/e2e/a11y.spec.ts` runs axe-core 4.14 (WCAG 2.0 and 2.1, levels A and AA) on 21 route and viewport combinations, at 1280 px and at 390 px for the two phone routes. The combinations include `/settings?tab=users`. The last run found no violations of any impact. No change was needed for that run.

The scan does not open dialogs, show error states or fill lists with real data. Those states are covered by unit tests and by reading the code. The new dialogs in this phase (reset password, deactivate confirmation, member created notice) use the shared `Dialog`, which traps focus and returns it to the opener, and the shared `Field`, which links labels, hints and errors.

## Users tab (owner only)

The tab is removed for members (`settingsTabsFor`). A member who opens `?tab=users` sees the AI tab (`effectiveSettingsTab`). The server still returns 403 for every `/users` route, so the hidden tab is not the access control. The section lists accounts with their role and status in words, creates members (the initial password is shown once, with a note to share it out of band), deactivates and reactivates members after a confirmation, and resets passwords. Errors are shown as text. Owner rows have no actions.

## Still not applied

* Wake word: no engine. Its row stays "Not implemented yet".
* Interruptions depend on the browser firing `speechstart` while speech plays. Some browsers do not, so the setting may do nothing there. The Voice page does not claim otherwise.
* Continuous listening stops when the browser ends recognition with an error that is not `no-speech` or `aborted`.
* Browser alerts need an open page. There are no push notifications.
* Summary memories are not embedded until the reindex action runs.
* The "Stop speaking when I talk" option is saved on the Voice page too, so it is available in two places. Both write the same setting.

## Tests

Vitest suites added in phase 3:

* `src/__tests__/web-phase3-rules.test.ts`: browser alert rules (hidden tab only, flags, generic text, task details, permission request, unsupported browsers), export file names and the download flow with a mocked fetch and object URL, deep-link parsing and the 2000-character cap, tabs by role, member account rules, push-to-talk mode.
* `src/__tests__/web-phase3-ui.test.tsx`: hidden-notifications note, export buttons, composer prefill (no send until Enter), the Users section (actions only for members, form errors before sending, the password shown once), automation saves that send only the changed key and reject out-of-range timeouts.
* `src/__tests__/web-b-integrations.test.tsx`: disconnect copy is the same for every provider.

Every test here uses a mocked fetch or a mocked browser API. None of them talks to a live backend or a real microphone or notification service.
