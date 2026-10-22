# Late results and schedule lateness (phase 2, area `late`)

This note covers two backend rules. It adds no endpoints, no request fields and no error codes. Clients see the
results through the existing task timeline, conversation messages, audit log and schedule run history.

## 1. Results that arrive after a task already ended

A device result is matched to its task by command id and device id. Tasks in `running` take the normal result path.
A result for a task that already ended follows these rules. Every rule runs under the same row lock as dispatch,
cancel and the timeout sweep, so a result and a timeout racing on one running task end in exactly one final state.

| Task before the result | Agent result | Final task status | Timeline (`TaskEvent`) | Chat follow-up |
|---|---|---|---|---|
| `failed`, error `timeout` | `succeeded` | `completed` (`result` set from the output, `error` null) | `late_result` then `completed` | yes |
| `failed`, error `timeout` | `failed` | stays `failed` (error stays `timeout`) | `late_result` | no |
| `cancelled`, error `cancelled_by_user`, a command was sent | `succeeded` | stays `cancelled` (`result` is not set) | `late_result` | yes |
| anything else | any | unchanged | none | none |

A second result for a task that already has a `late_result` event is ignored. So is a second result for a task that
already completed or failed normally. A result for an unknown command id is ignored. Ignored results are logged at info.

### Timeline event `late_result`

`TaskEvent.type = "late_result"`. The `data` values are:

- After a timeout, success: `{"status": "succeeded", "reported_after": "timeout"}`. The message is
  `The device reported success after the time limit. The task is now completed.` The `completed` event that follows
  carries the summary as its message.
- After a timeout, failure: `{"status": "failed"}`. The message is
  `The device reported a failure after the time limit. The task stays failed.`
- After a user cancel, success: `{"status": "succeeded", "reported_after": "cancel"}`. The message is
  `The operation completed even though it was cancelled.`

### Chat follow-up

Written only for the two success rows. It is an assistant message with `task_id` set and
`meta = {"kind": "late_result", "task_status": <status>, "reported_after": "timeout" | "cancel"}`. The text uses only the
summary the device sent, with its trailing period removed. The sentence is printed without a summary when the device sent none.

- After a timeout: `The earlier request finished after it timed out: <summary>.`
- After a cancel: `The action completed even though you cancelled it: <summary>.`
- When the desktop could not verify the action (`verified: false`), the lead gains `, but the result is not confirmed`,
  as in `The earlier request finished after it timed out, but the result is not confirmed: <summary>.`

The follow-up is written after the timeout outcome message, which is `Not confirmed yet: <title>. <message>` for a task
that was sent to the device and then timed out. A device failure still reads `It did not work: ...`. Nothing is written when
conversation storage is off (`privacy.store_conversations` false) or the task has no conversation. The web client
receives the usual `chat.message` event for the new message.

### Audit

Action `tasks.late_result`, target `task`, actor `device`. `data` holds `tool`, `status`, `reported_after`,
`task_status` and `follow_up` (true when a chat message was written).

### State machine

`failed -> completed` is the only edge added, and it exists only in `apply_late_result`
(`app/services/task_states.py`). It refuses any task that is not `failed` with error code `timeout`.
`ALLOWED_TRANSITIONS` is unchanged, and `failed`, `completed` and `cancelled` are still terminal on every other path.

### Timeout sweep

The sweep selects only `running` tasks for timeout, under `FOR UPDATE`. A task that a late result completed is never
selected again, so the sweep cannot overwrite it.

A running task that the user asked to cancel, and that the device never confirmed, ends `cancelled` with error
`cancelled_by_user` and the message `Cancel requested. The device did not confirm the stop in time, so the action may
still finish.` It does not end `failed`. A running task without a cancel request that times out ends `failed` with error
`timeout`.

### Background sweeps in the app lifespan

With `RUN_BACKGROUND_WORKERS` true, the lifespan starts the task sweep loop (`jarvis-task-sweeps`, every
`SWEEP_INTERVAL_SECONDS`, 5 seconds in production) and the scheduler. Both stop cleanly on shutdown.

## 2. Late recurring schedule slots

A daily or weekly slot is late when the scheduler reaches it more than 15 minutes after its time (`LATE_SLOT_LIMIT`).
The check is strict, so exactly 15 minutes still fires and 16 minutes is skipped.

For a late recurring slot:

- A `ScheduleRun` row is written with `status = "skipped"`, `attempt = 1`, `scheduled_for` set to the slot,
  `task_id` null, `finished_at` set to now, and `error = {"code": "too_late", "message": "The slot was more than 15 minutes late, so this run was skipped."}`.
- Nothing is proposed to the task engine.
- `ScheduledJob.next_run_at` moves to the next occurrence after now. `last_status` becomes `skipped`.
  `last_run_at` does not change, because nothing ran. `consecutive_failures` does not change.
- A second pass over the same slot writes no second row, because the run row is unique per (job, slot, attempt).

A once schedule is never skipped for lateness. It fires late, as designed.

## Limitations

- A success after a timeout completes the task but does not send a new notification. The timeline and the chat
  follow-up are the record.
- A scheduled run that already settled as `failed` after a timeout keeps that status when its task later completes.
  The schedule history does not follow late results.
- After a cancel, a device that never confirms the stop may still complete the action on the device. The task stays
  `cancelled`, and the follow-up message says so.
