# Desktop agent protocol

The Windows desktop agent (`apps/desktop`) holds a long-lived WebSocket to the backend. The backend can only ask it to perform **typed actions** from a fixed list. There is no shell, no free-form command and no file path supplied by the model.

## 1. Pairing

1. The user opens **Connected Devices → Pair a device** in the web app. The backend creates a one-time code `XXXX-XXXX` (alphabet `ABCDEFGHJKMNPQRSTUVWXYZ23456789`), valid for `DEVICE_PAIRING_CODE_TTL_SECONDS` (default 600). Only a keyed hash is stored.
2. The agent calls `POST {server}/api/v1/agent/pair` with the code and its description:

```json
{
  "code": "KQ7M-2XBV",
  "device_name": "Laptop",
  "device_type": "laptop",
  "os": "windows",
  "os_version": "10.0.22631",
  "agent_version": "1.0.0",
  "capabilities": ["open_url", "launch_app", "open_folder", "open_project", "system_info", "read_file", "app_availability"],
  "resources": {
    "apps": [{"id": "chrome", "label": "Google Chrome"}, {"id": "vscode", "label": "Visual Studio Code"}],
    "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}],
    "folders": [{"id": "downloads", "label": "Downloads"}],
    "projects": [{"id": "jarvis", "label": "JARVIS project"}],
    "files": [{"id": "notes", "label": "Meeting notes"}]
  }
}
```

3. The backend returns `{device_id, device_secret, websocket_path, heartbeat_interval_seconds, protocol_version}`. `device_secret` is shown to the agent once and stored by the agent in Electron `safeStorage` (or an owner-only file in headless mode). The backend stores only a SHA-256 of it for lookup and an encrypted copy for signing.

Wrong codes count against `DEVICE_MAX_PAIRING_ATTEMPTS`. After that the code is invalidated.

`resources` contains **ids and labels only**. Local paths stay on the device. `url` is included for URL resources because the backend must check the requested URL against the approved list.

## 2. Connection

`GET wss://{host}/api/v1/agent/ws` with header `Authorization: Bearer <device_secret>`.

| Close code | Meaning | Agent behaviour |
|---|---|---|
| 1000 | normal close | reconnect with backoff |
| 4401 | invalid or unknown secret | stop and show "re-pair required" |
| 4403 | device revoked | stop, clear stored secret |
| 4408 | no hello within 10 s | reconnect |
| 4409 | replaced by a newer connection | stop this connection |

Reconnect backoff: 1 s, doubling, capped at 60 s, with jitter.

## 3. Messages

Every frame is a JSON text message with a `type`. Unknown types are ignored and logged.

### Server → agent

**`hello_ack`** — sent immediately after a valid handshake.
```json
{"type": "hello_ack", "device_id": "…", "server_time": "2026-10-19T04:51:37Z", "heartbeat_interval_seconds": 15, "protocol_version": 1}
```

**`command`** — an approved action to execute.
```json
{
  "type": "command",
  "id": "9b7e…",                      // command id (UUID)
  "action": "launch_app",
  "args_json": "{\"app_id\":\"chrome\"}",
  "issued_at": "2026-10-19T04:51:37Z",
  "expires_at": "2026-10-19T04:52:37Z",
  "nonce": "b64url…",
  "signature": "hex…",
  "protocol_version": 1
}
```

**`cancel`** — ask the agent to stop a running command.
```json
{"type": "cancel", "command_id": "9b7e…", "issued_at": "…", "expires_at": "…", "nonce": "…", "signature": "hex…"}
```

**`ping`** — connectivity check. `{"type": "ping", "nonce": "…", "sent_at": "…"}`

**`error`** — protocol error from the server. `{"type": "error", "code": "…", "message": "…"}`

### Agent → server

**`hello`** — first frame after the handshake, within 10 s.
```json
{"type": "hello", "agent_version": "1.0.0", "os": "windows", "os_version": "10.0.22631", "capabilities": ["launch_app"], "resources": {"apps": []}}
```
If `capabilities` or `resources` differ from the stored values, the backend updates them and publishes `device.updated`.

**`heartbeat`** — every `heartbeat_interval_seconds`. `{"type": "heartbeat", "running_command_ids": ["…"], "sent_at": "…"}`. Three missed heartbeats (about 45 s) mark the device offline and close the socket.

**`progress`** — only when the agent can measure progress. `{"type": "progress", "command_id": "…", "step": "Waiting for window", "message": "", "progress": null, "sent_at": "…"}`. Never send an estimated `progress`.

**`result`** — exactly once per command.
```json
{
  "type": "result",
  "command_id": "9b7e…",
  "status": "succeeded",
  "output": {"pid": 4312, "app_id": "chrome", "verified": false, "summary": "Started Google Chrome (pid 4312)."},
  "error": null,
  "started_at": "…",
  "finished_at": "…"
}
```
`status` is `succeeded`, `failed` or `cancelled`. On failure `error` is `{"code": "...", "message": "...", "details": {}}`.

**`pong`** — `{"type": "pong", "nonce": "…"}`.

## 4. Command verification (agent MUST)

1. Parse the frame. Require `protocol_version == 1`.
2. Recompute the HMAC-SHA256 over the six fields joined with `\n` in this order: `id`, `action`, `issued_at`, `expires_at`, `nonce`, `args_json` (the exact received strings), using the UTF-8 bytes of `device_secret` as the key. Compare in constant time to `signature` (hex, lowercase). Reject on mismatch.
3. Require `now < expires_at` and `issued_at ≤ now + 30 s` (clock skew).
4. Reject if `id` or `nonce` was seen in the last 10 minutes (replay).
5. Require `action` to be one of the supported actions below **and** its capability (the same name as the action) to be enabled in the local config. `run_check` is enabled only while the local config holds at least one valid approved check.
6. Parse `args_json` and validate it against the action's schema. Reject extra keys.
7. Resolve every resource id against the **local** config. Refuse ids that are not configured. The server's view is never trusted for paths.

A rejected command produces a `result` with `status: failed` and an error code (`invalid_arguments`, `permission_denied`, `expired`, `replayed`, `unsupported_action`). A frame that fails its signature (`signature_invalid`) gets no result, because its command id is not authenticated. The agent also refuses a command whose `expires_at - issued_at` is longer than 540 seconds, the longest TTL the backend issues, so that the 10-minute replay record always outlives the command. Replay records are kept in `replay-store.json` in the agent's data directory and survive restarts, so a command recorded before a restart is still rejected after it. A missing file means a first start, with an empty store, unless the device is paired: a paired device must have a file, and pairing creates an empty one. A corrupt, unreadable or missing-when-paired file is never treated as empty. Until a new file is written, the agent refuses any command whose `issued_at` is before the agent started. That floor is written into the file (`issued_floor_ms`) and ends one replay window (10 minutes) after start-up.

## 5. Actions

| Action | Capability | Args (`args_json`) | Output on success |
|---|---|---|---|
| `launch_app` | `launch_app` | `{"app_id": "chrome"}` | `{pid, app_id, verified: false, summary}` |
| `open_url` | `open_url` | `{"url_id": "youtube"}` or `{"url": "https://…"}` where the URL exactly equals an approved entry | `{url, verified: false, summary}` |
| `open_folder` | `open_folder` | `{"folder_id": "downloads"}` | `{folder_id, verified: false, summary}` |
| `open_project` | `open_project` | `{"project_id": "jarvis", "app_id": "vscode"}` (`app_id` optional, default `vscode`) | `{project_id, app_id, pid, summary}` |
| `system_info` | `system_info` | `{}` | `{hostname, os, os_version, arch, cpu_count, total_memory_mb, free_memory_mb, uptime_seconds}` |
| `read_file` | `read_file` | `{"file_id": "notes"}` | `{file_id, content, truncated, size_bytes}` (max 64 KB) |
| `app_availability` | `app_availability` | `{"app_id": "chrome"}` | `{app_id, installed: true\|false, summary}` |
| `run_check` | `run_check` | `{"check_id": "tests"}` | `{check_id, label, exit_code: 0, output_tail, truncated, duration_ms, summary}`. A failed run sends the same fields with `exit_code` set and error `check_failed`, or `timeout` (then `exit_code` is `null`). |

`run_check` runs one check that the owner defined in the agent's own `agent.json` (`approvedChecks`: an absolute `command`, an `args` array, an absolute `cwd`, `timeoutSeconds`). The backend can name only the check's id. The command line never travels over the wire, and no shell is used. Output is captured as text, capped at its last 4 KB, and never interpreted. `run_check` is never reported as started, so a cancel stops it by killing its process tree.

Honesty rules for outputs:

* `verified: false` means the agent started or requested the operation but cannot confirm what the user sees. For example `open_url` asks the default browser to open the URL.
* Chrome, VS Code and other executables are located through known install paths and `PATH`-free absolute candidates. If none exists the result is `failed` with `not_installed`. It is never reported as succeeded.
* The agent spawns executables without a shell (`shell: false`), with explicit argument arrays, `windowsHide: true` and `detached: true`. It confirms the process started (`spawn` event with a pid) before reporting success.

## 6. Cancellation

`cancel` names a running `command_id`. The agent aborts what it can (for example a pending `read_file` or a process watch). It then sends `result` with `status: cancelled` and `error.code = cancelled_by_user`. A command that already finished is answered with its existing result. Launching a process that has already started cannot be undone by the agent. The result says so.

## 7. Idempotency and reconnects

* The backend deduplicates results by `command_id`. Repeated results are ignored.
* The agent keeps unsent results in an outbox file and resends them after reconnecting, until the backend acknowledges the connection. The backend answers an outbox resend with the current task state, not with a repeat of the action.
* If the connection drops while a command is running, the task remains `running` until the agent reconnects and sends its result, or until `DEVICE_COMMAND_TIMEOUT_SECONDS` elapses. Then the task fails with `timeout`. The backend never re-sends a command that the agent might already have run. Retries are created as new tasks by the user.

## 8. Security notes

* Pairing codes and device secrets are high-entropy or single-use. Revocation closes the socket and deletes the server-side encrypted secret.
* WSS (TLS) is required in production. Plain `ws://` is accepted only for `localhost` development.
* Commands travel only over the authenticated socket and are signed in addition, so a stolen socket still cannot forge a command without the secret.
