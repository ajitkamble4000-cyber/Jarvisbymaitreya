# JARVIS Windows desktop agent

`apps/desktop` is the companion that runs on a paired PC. It holds one WebSocket to the JARVIS backend, verifies every
signed command, and runs only the typed actions in `docs/desktop-protocol.md`, using the local `agent.json` as the only
authority for what may run. The backend can name ids; it never supplies a path, an executable or a shell command.

The core runs on any system with Node 22. Real actions happen only through `WindowsPlatform`. `SimulatedPlatform`
records calls and performs nothing; its results say `SIMULATED` and the UI shows the banner
`SIMULATED PLATFORM - no real actions performed`.

## Layout

| Path | Purpose |
|---|---|
| `src/shared/` | Wire types and zod schemas (`protocol.ts`), HMAC signing (`canonical.ts`), IPC names and payload schemas |
| `src/agent/` | Agent core: config, secret store, pairing, connection, command verifier, outbox, dispatcher, actions |
| `src/platform/` | Platform adapter: `platform.ts` (interface), `windows.ts` (real), `simulated.ts` (simulated) |
| `src/cli/headless.ts` | Headless runner for development and the end-to-end check |
| `src/main/`, `src/preload/`, `src/renderer/` | Electron shell: main process, sandboxed preload, plain TypeScript renderer |
| `scripts/build-bundles.mjs` | Bundles the sandboxed preload and the renderer with esbuild |
| `test/` | Vitest suite, including a fake backend WebSocket server (test double only) |

## Setup

```
cd apps/desktop
npm install                                # on Linux CI: ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm install
```

All versions are pinned exactly in `package.json` (Electron 44.9.0, ws 8.22.0, vitest 5.0.5, TypeScript 5.9.3,
electron-builder 26.15.3, zod 4.6.7, esbuild 0.28.2).

## Commands

| Command | What it does |
|---|---|
| `npm run typecheck` | `tsc -p tsconfig.json --noEmit`, strict, covers `src/` and `test/` including the Electron typings |
| `npm test` | Vitest unit and integration tests (no Electron binary needed) |
| `npm run build:node` | `tsc -p tsconfig.build.json`: compiles the non-renderer code to `dist/` (CommonJS) |
| `npm run build:bundles` | `node scripts/build-bundles.mjs`: writes `dist/preload/index.js` and `dist/renderer/{index.html,main.js,styles.css}` |
| `npm run build` | Both build steps |
| `npm run headless -- --help` | Usage for the headless runner |
| `npm run headless -- --server http://127.0.0.1:8000 --code XXXX-XXXX --platform simulated` | Pairs with a code from the web app and runs the simulated agent |
| `npm start` | Builds and runs Electron (Windows development) |
| `npm run dist` | Builds an NSIS installer with electron-builder (Windows) |

Headless options: `--server`, `--code`, `--platform simulated|windows`, `--device-name`, `--data-dir`,
`--run-seconds`, `--verbose`, `--help`. Use `--platform windows` only on a Windows PC. Without `--data-dir` the
headless runner uses `%APPDATA%\JARVIS` on Windows and `~/.config/JARVIS` elsewhere. Pass `--data-dir` when testing
on a development machine so that no real configuration is touched.

## Pairing

1. In the web app, open Connected Devices and create a pairing code (`XXXX-XXXX`, single use, time limited).
2. Enter the server address and the code in the agent window, or pass `--server` and `--code` to the headless runner.
3. The agent posts its description to `POST {server}/api/v1/agent/pair`. The response's `device_secret` goes to the
   secret store. The config records the device id, server origin, websocket path and heartbeat interval.

The code and the secret are never logged and never written to `agent.json`. Pairing is refused when the secret store
reports that it is unavailable (in the Electron app that means `safeStorage` is not usable). The server receives ids, labels and URL strings for URL entries; local paths stay on the PC.

## Local configuration: `agent.json`

Location: `%APPDATA%\JARVIS\agent.json` (Windows). The file is created by the agent once it is saved; if it does not
exist, the defaults below apply.

```json
{
  "version": 1,
  "approvedApps": [
    { "id": "chrome", "label": "Google Chrome",
      "windows": { "candidates": ["%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe", "%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe", "%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe"] } },
    { "id": "vscode", "label": "Visual Studio Code",
      "windows": { "candidates": ["%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe", "%ProgramFiles%\\Microsoft VS Code\\Code.exe", "%ProgramFiles(x86)%\\Microsoft VS Code\\Code.exe"] } }
  ],
  "approvedUrls": [],
  "approvedFolders": [],
  "approvedProjects": [],
  "approvedFiles": [],
  "capabilities": { "open_url": true, "launch_app": true, "open_folder": true, "open_project": true,
                    "system_info": true, "read_file": true, "app_availability": true },
  "pairing": null
}
```

* Approved entries are added by hand. Ids follow the rule `^[a-z0-9][a-z0-9_-]{0,63}$`. Unknown top-level keys and
  unknown capability names are rejected; the file fails to load with a message that does not echo its contents.
* A capability that is missing or `false` is disabled. A command for a disabled capability fails with `permission_denied`.
* Executables are looked up only among the absolute candidate paths built from `%VARIABLE%` templates. PATH is never
  searched. A template whose variable is missing is dropped.
* `approvedUrls` entries must be `http` or `https`. An `open_url` command must match an entry exactly.
* `approvedFiles` are read only through the `read_file` action: at most 64 KB, and symbolic links and paths that pass
  through a link are refused.

## Approved project checks (`run_check`)

A check lets the backend run one command that you defined here, for example the test suite of a project. The backend
can name a check by its id only. It cannot define, change or add a command.

```json
"approvedChecks": [
  {
    "id": "jarvis-tests",
    "label": "JARVIS unit tests",
    "command": "C:\\Program Files\\nodejs\\node.exe",
    "args": ["node_modules\\vitest\\vitest.mjs", "run"],
    "cwd": "C:\\dev\\jarvis-ai\\apps\\desktop",
    "timeoutSeconds": 300
  }
]
```

* `command` must be an absolute path to an executable file. The agent starts it directly with the `args` array and no
  shell, so `npm` and other `.cmd` or `.bat` wrappers cannot be used: name the real executable (for example `node.exe`
  with the script as its first argument).
* `args`: at most 32 items, each at most 200 characters and without NUL. Default `[]`.
* `cwd` must be an absolute folder. `timeoutSeconds` is 10 to 600 (default 300).
* An entry with an unknown field, a relative path, a timeout outside the range, or a bad or repeated id is skipped and
  logged. A check whose command is not a file, or whose folder does not exist, is skipped at load time. A skipped check
  never runs.
* `run_check` is advertised to the backend only while at least one valid check is configured. Only `{id, label}` is sent.
* The process gets only two environment variables: `PATH` (so the check can find its own tools) and `SystemRoot` (the
  Windows folder that programs need to start). Nothing else from the agent's environment is passed on.
* Output: the last 4 KB of stdout and stderr, as text. Exit 0 succeeds. A non-zero exit fails with `check_failed` and
  the same output. A run longer than `timeoutSeconds` is stopped and fails with `timeout`. A cancel from the backend
  kills the process tree and reports `cancelled_by_user`.
* Every run needs approval in the chat, because the backend marks the tool as high risk.

## Local state files

| File | Contents | Written by |
|---|---|---|
| `agent.json` | Approved resources, capabilities, pairing record (no secret) | Agent, after pairing or unpairing |
| `outbox.json` | Command results not yet accepted by the backend | Agent, before every send |
| `replay-store.json` | Command ids, nonces and times seen in the last 10 minutes (replay protection) | Agent, before a command runs |
| `secrets.bin.json` | Device secret, encrypted with Electron `safeStorage` (Windows: DPAPI) | Electron build |
| `secrets.json` | Device secret in plain text, owner-only file | Headless runner and tests only |

## Protocol behaviour

* Connection: `wss://{host}/api/v1/agent/ws` with `Authorization: Bearer <device_secret>`. The agent sends `hello`
  right after the handshake. Close codes follow `docs/desktop-protocol.md` section 2: 4401 requires re-pairing,
  4403 clears the local pairing, 4409 stops this connection, and everything else reconnects with 1 s backoff, doubling
  to 60 s, with 75 to 100 percent jitter.
* Commands are verified in the protocol's seven steps: signature (constant-time), expiry, clock skew (30 s), replay
  (ids and nonces, 10 minutes), action and capability, strict argument schema, and local resource resolution.
  A failed command gets a `failed` result. Signature failures give no detail.
* Results are written to `outbox.json` before they are sent. An entry is removed once a later heartbeat, or a resend
  after reconnecting, has been written without error.
* Cancellation works only before an action has produced a pid or handed a request to the operating system. After that,
  the result says the action cannot be undone.

**Replay protection.** Every command and cancel that passes its signature check is recorded as `{id, nonce, seen_at}`
in `replay-store.json` before a command can run, and each record is kept for 10 minutes. A command whose
`expires_at - issued_at` is longer than 540 seconds is refused. The file is written atomically (temporary file, fsync,
then rename), keeps only live records, and holds at most 10,000 entries, dropping the oldest with a warning. A restarted
agent loads it and rejects every command it already recorded. A missing file means a first start. A corrupt or
unreadable file is never treated as empty: the agent logs that replay protection is degraded and refuses any command
issued before it started, until a successful write recreates the file. A missing file is degraded too once the device is
paired. If a record cannot be written, the command is not run and its result is `agent_error`. The owner-only file mode
has no effect on Windows (see Security limitations).

## Electron shell

* `BrowserWindow`: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity: true`, no
  webviews, no devtools in packaged builds.
* Content-Security-Policy meta tag: `default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'none'` (plus
  `object-src`, `frame-src`, `base-uri` and `form-action` restrictions).
* Navigation, redirects, new windows and permission requests are all denied. IPC payloads are validated with zod, and
  the preload exposes only the typed `window.jarvis` object.
* Single-instance lock. The tray menu shows status, Start, Stop, Open window and Quit. Closing the window keeps the
  agent running.

## Security limitations

* On Windows the POSIX owner-only mode bits have no effect. `agent.json`, `outbox.json` and `secrets.json` rely on the
  user's `%APPDATA%` folder, which Windows already restricts to that user. Only `secrets.bin.json` (safeStorage) is
  encrypted at rest; the headless `secrets.json` is plain text and is for development only.
* The agent does not sign the installer (`signAndEditExecutable: false`) and no code-signing certificate is configured.

## Not verified in this repository

* Electron itself has not been started. The binary is not downloaded in the build sandbox, so the window, CSP,
  tray and `safeStorage` paths are verified only by type checking and by unit tests of the modules they call.
* `WindowsPlatform` has been exercised only with fake child processes and a fake environment on Linux. Real launches of
  Chrome and VS Code have not been run.
* Approved checks: the Windows process-tree kill (`taskkill /T /F /PID`) is written but has not been run on Windows.
  Windows exit codes, the `SystemRoot` and `PATH` handling, and the Windows paths in the examples are unverified.
* `npm run dist` (installer) has not been run.

## Quick window (Ctrl+Space)

While the agent runs, **Ctrl+Space** opens a small window that shows the web app's `/quick` page. Type a request, see the
reply, and approve actions in the same window. Esc or a click elsewhere closes it.

| Environment variable | Default | Purpose |
|---|---|---|
| `JARVIS_WEB_URL` | `http://127.0.0.1:5173` | Web app address. Only the origin is used. Https, or http on loopback only. |
| `JARVIS_HOTKEY` | `Control+Space` | Electron accelerator, such as `Alt+Space`. It needs a modifier and a key. An unusable value falls back with a warning. |

The tray menu shows the shortcut, any warning, and **Start with Windows** (installed app only). The popup is refused any
address outside the web app's origin, and it gets the microphone only from that origin.

## Building for Windows

```powershell
npm run dist                 # NSIS installer in release\ (run on Windows)
```

On Linux, the installer step needs Wine. A portable zip does not:

```sh
npm run build
npx electron-builder --config electron-builder.yml --win zip --x64 --publish never
```

The zip contains `JARVIS Agent.exe`. Unzip it on Windows and run it. Desktop tests: `npx vitest run` (248 tests).
