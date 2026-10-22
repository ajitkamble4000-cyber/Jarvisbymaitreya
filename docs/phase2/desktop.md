# Phase 2: persistent replay protection for the desktop agent (contract for the web and verification agents)

Scope: the desktop agent in `apps/desktop` keeps its replay records across restarts. Before a command runs, the agent records its id and nonce in a file, so a command captured before a restart is still refused after it. This document does not edit `docs/api.md`. The wire protocol is in `docs/desktop-protocol.md` section 4, and the user-facing notes are in `apps/desktop/README.md`.

## Endpoints

None. This change is local to the agent and adds no backend route or message type.

## Local state

| File | Purpose |
| --- | --- |
| `<data dir>/replay-store.json` | `{"version": 1, "entries": [{"id", "nonce", "seen_at"}]}`, where `seen_at` is epoch milliseconds. Holds at most 10,000 entries, each kept for 10 minutes. Owner-only on POSIX. |

The data directory is the agent's existing one: `%APPDATA%\JARVIS` on Windows, `~/.config/JARVIS` elsewhere, or `--data-dir` for the headless runner.

## Behaviour

| Situation | Result |
| --- | --- |
| Command accepted at step 4 of the verifier | Record written, atomically, before the command is submitted. Refused with `agent_error` if the write fails, and not run. |
| Command or cancel id or nonce already recorded, including before a restart | Refused with `replayed`. An existing outbox result is resent; nothing runs again. |
| `expires_at - issued_at` above 540 s | Refused with `invalid_arguments`, as for other timestamp problems. |
| File missing at start | Starts empty, not degraded. |
| File corrupt, not valid for version 1, or unreadable | Degraded. Commands with `issued_at` before start-up are refused with `replayed`. A warning is logged. Degraded mode ends when a successful write recreates the file. The refusal floor stays in the file (`issued_floor_ms`), so it survives restarts and ends one replay window (10 minutes) after start-up. |
| File missing, but the device is paired | Degraded, as for a corrupt file. A paired device has run commands. Pairing creates an empty store, so a normal restart is not affected. |
| Cancel frame | Same replay rules, applied to its nonce. A cancel cannot overtake the command it names, because inbound frames are handled in arrival order. |

## Environment

No new variables. Existing: `--data-dir` for the headless runner.

## Verification

- `cd apps/desktop && npx tsc -p tsconfig.json --noEmit`
- `cd apps/desktop && npx vitest run`: includes `test/replay-store.test.ts` (store file behaviour, degraded mode, restart across two agent instances, a failed write stops the command) and the new rules in `test/verifier.test.ts`.

## Limits

- On Windows the POSIX owner-only mode is not enforced. The file relies on the user's `%APPDATA%` folder, as the other state files do.
- While the store is degraded, every command issued before start-up is refused, including the last minute before it. A device whose clock lags the backend by more than a minute loses those commands for the same period.
- A missing file is indistinguishable from a first start, so deleting it resets the store.
- Two agent processes sharing one data directory each keep their own records, and the last write wins.
