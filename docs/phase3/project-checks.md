# Approved project checks (`run_check`)

The assistant can run one pre-approved check on a paired PC, for example "Run my tests" or a nightly test run.
The check is defined only in the desktop agent's own `agent.json` (`approvedChecks`). The backend and the chat can
name a check by id or label. They cannot define, change or add a command.

## Assistant tool

| Field | Value |
|---|---|
| Tool name | `run_project_check` |
| Target | device (`ExecutionTarget.DEVICE`) |
| Device action | `run_check` |
| Required capability | `run_check` |
| Risk | `high` |
| Approval | always required, also when the device permission is `allow` |
| Category | `development` |
| Status | `working` |

Arguments (`extra` keys are refused):

```json
{ "device": "Laptop", "check": "JARVIS unit tests" }
```

* `device`: optional device name, as for every device tool.
* `check`: required, 1 to 100 characters. It is a check id or label, matched case-insensitively. No slashes, backslashes
  or control characters.

## Wire command (agent, signed like every command)

The backend resolves `check` against the device's reported `checks` resource and sends only:

```json
{ "check_id": "jarvis-tests" }
```

The backend never sends a command line, an argv, a path or a working folder.

## Device resources

The agent reports approved checks in `hello.resources.checks` as `{id, label}` entries. The sanitiser
(`app/schemas/devices.py`, `sanitise_resources`) keeps only `id` and `label`, at most 50 entries, with the same id
pattern as apps (`^[a-z0-9][a-z0-9_-]{0,63}$`) and labels of 1 to 120 characters. Any `argv`, `command`, `cwd`,
`path` or other field a client sends is dropped.

The device's `capabilities` include `run_check` only when the agent has at least one valid check.

## Permissions

`run_check` is a valid capability in `app/devices/protocol.py` (`DEVICE_ACTIONS`). The default mode is `ask`.

| Request | Result |
|---|---|
| `PATCH` device permissions `{"run_check": {"mode": "ask"}}` | accepted |
| `{"run_check": {"mode": "deny"}}` | accepted |
| `{"run_check": {"mode": "allow"}}` | 422 `permission_not_allowed` (HIGH risk) |

If a stored `allow` is present anyway, the engine still asks, because `effective_mode` only honours `allow` for LOW risk
tools and the tool sets `approval_required`.

## Refusals (proposal time)

| Code | When |
|---|---|
| `capability_missing` | the device does not report `run_check` (no valid check configured on the agent) |
| `permission_denied` | the device permission for `run_check` is `deny` |
| `resource_not_approved` | the check id or label is not on the device. `details.approved` lists the approved labels. |
| `invalid_arguments` | unknown keys, or a check that is empty, too long, or contains a path or control character |
| `device_not_found`, `needs_device_selection` | as for other device tools |

## Results from the agent

| Status | Error code | Output |
|---|---|---|
| succeeded | none | `{check_id, label, exit_code: 0, output_tail, truncated, duration_ms, summary}` |
| failed | `check_failed` | the same fields, with `exit_code` set to the non-zero code |
| failed | `timeout` | the same fields, with `exit_code: null` |
| cancelled | `cancelled_by_user` | none |
| failed | `not_installed` / `execution_failed` | none |

`check_failed` and `timeout` are in `AGENT_ERROR_CODES` (`app/services/task_engine.py`). An unknown code is still
stored as `execution_failed`.

## Timeout: `automation.default_task_timeout_seconds`

The setting (10 to 1800, default 120, `PATCH /settings`) is now used. The task engine reads it when a task is
dispatched and stores the deadline on the task:

* `tasks.run_deadline_at` (new, nullable, migration `7c3f0a9d2e61`, which is merged into head `d7a09bd3dc82`) =
  dispatch time + the user's setting. Without a valid stored setting, the configured `DEVICE_COMMAND_TIMEOUT_SECONDS`
  applies, as before.
* The running-task sweep fails a running task with `timeout` when `run_deadline_at <= now`.
* Rows dispatched before the column existed (`run_deadline_at IS NULL`) keep the old rule: `started_at` plus the
  configured device timeout.
* `revert_dispatch` clears the deadline when a command is not accepted by the hub.

## Security notes

* No shell anywhere. The agent starts the executable with an argv array, `shell: false` and `windowsHide: true`.
* The command, its arguments and its folder exist only in the agent's `agent.json`. A check cannot be defined or changed
  from the server, the chat or the API.
* Approval is always required for `run_project_check`, so a schedule that runs it waits for the user each time.
* Output is captured as text, capped at 4 KB (the last bytes), and is never interpreted.
* Logs record the check index and a reason, never the command line or the output.

## Environment variables

None new. The existing `DEVICE_COMMAND_TIMEOUT_SECONDS` is the fallback timeout described above.

## Desktop agent

The agent side (config, `run-check` action, `runCapture` platform method, minimal environment, process-tree kill) is
documented in `apps/desktop/README.md` ("Approved project checks") and in `docs/desktop-protocol.md` sections 4 and 5.
