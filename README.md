# JARVIS AI

A personal AI assistant with a web command centre, a FastAPI backend, and a Windows desktop agent.
The assistant can talk, plan, and propose actions on a paired computer. It never runs anything on its
own: every device action is a typed request that the server validates, the user approves, and the
desktop agent checks against its local allowlist before it runs.

Run the milestone flow, check what is verified, and read the limitations before relying on any feature.

**New here?** Start with [docs/TUTORIAL.md](docs/TUTORIAL.md): what JARVIS is, how to install it from GitHub, pair your PC, and use Ctrl+Space and "Hey JARVIS".

## What is in the repository

| Path | What it is |
|---|---|
| `services/api` | FastAPI backend: auth, AI providers, conversations, task engine, device hub, scheduler, memory, analytics. PostgreSQL via SQLAlchemy, Alembic migrations. |
| `apps/web` | React 19 web app (Vite, Tailwind CSS 4, React Router, TanStack Query). |
| `apps/desktop` | Windows desktop agent (Electron 44, TypeScript). The core runs headless on any OS for tests. |
| `apps/ipad-companion` | iPad companion: design and platform limits only, not built. See its README. |
| `tests/e2e` | Playwright milestone test (browser, real backend, fake Gemini). |
| `docs` | API contract, desktop protocol, architecture, operations and backup. |
| `scripts` | Development launch scripts. |

## Requirements

- Python 3.13, Node.js 22, npm.
- PostgreSQL 16 with the `pgvector` extension. Migration `5b7e2d91c0a4` (semantic memory) creates it, so every database must have it installed (see step 1).
- A Chromium build for the Playwright test. The e2e config uses `/opt/pw-browsers/chromium` when that path exists, or Playwright's bundled browser otherwise.
- Windows 10/11 only for the real desktop agent. Everything else runs on Linux or macOS with the simulated platform.

## Run it locally

1. Create the database (once):
   ```sh
   createuser -P jarvis          # password: jarvis (matches the dev default)
   createdb -O jarvis jarvis
   createdb -O jarvis jarvis_test
   # Run as a superuser. The jarvis role cannot create the pgvector extension itself, so migrations fail
   # with "permission denied to create extension" unless it is already installed in the database.
   psql -d jarvis -c 'CREATE EXTENSION IF NOT EXISTS vector;'
   psql -d jarvis_test -c 'CREATE EXTENSION IF NOT EXISTS vector;'
   ```
   The Playwright test drops and recreates `jarvis_e2e` on every run, so the extension must be installed in
   `template1` for it to work: `psql -d template1 -c 'CREATE EXTENSION IF NOT EXISTS vector;'` as a superuser.
2. Create the backend environment (once):
   ```sh
   cd services/api
   python3.13 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   cd ../..
   ```
3. Start the API. The first run creates `.env` from `.env.example`, generates an encryption key, and applies migrations:
   ```sh
   scripts/dev-backend.sh        # http://127.0.0.1:8000  (docs: /api/docs)
   ```
4. In a second terminal, start the web app (installs npm packages on first run):
   ```sh
   scripts/dev-web.sh            # http://127.0.0.1:5173
   ```
5. Open http://127.0.0.1:5173. On first run you create the owner account. Then:
   - **AI Models**: paste a Google Gemini API key, press *Save key*, then *Test connection*, then choose a model from the live list. Keys are encrypted at rest and never shown again.
   - **Connected Devices**: press *Create pairing code*, then enter the code in the desktop agent (below).
   - **Chat**: ask for an action, for example "open Google Chrome on my laptop". The request appears as a task that waits for your approval.

`.env` holds your local secrets and is git-ignored. Keep `ENCRYPTION_KEY` safe: losing it makes every stored
provider key unreadable. See [docs/operations.md](docs/operations.md).

### Docker Compose

`docker compose up --build` builds the API and web images and starts PostgreSQL with pgvector.

Verified in this sandbox on 2026-10-21 (phase 3), with the stack published on `127.0.0.1` (PostgreSQL on 5433 for this run):

- Both images built: API 303 MB, web 80 MB. `api` and `postgres` report healthy.
- Health through the API port and through the nginx proxy on 5173: `status` `ok`, `database` `ok`.
- `GET /api/v1/metrics` returned 404 while `METRICS_TOKEN` was unset. With a temporary token in `.env` (removed afterwards) it returned 401 without the header, 401 with a wrong one, and 200 with the right one, exposing the `jarvis_*` metric families.
- Owner setup through the proxy returned 201 with the Origin and CSRF header. A member created by the owner got 201. The member's `GET /api/v1/users` returned 403 `forbidden`. The member's devices list was empty while the owner's device was `online`.
- The owner's conversation exported as an attachment (`Verify export.md`, `Cache-Control: no-store`) and as JSON. The member asking for the same export got 404.
- A headless desktop agent (simulated platform) paired with the compose API and reported `online`.
- Teardown: `docker compose down -v` removed the containers, network and both volumes. The temporary CA copies were deleted from `extra-ca/`.

Earlier run on 2026-10-20 (not repeated in phase 3): forwarded-header handling (13 logins with forged `X-Forwarded-For`: 401 for the first ten, then 429), a state-changing request without the CSRF header returned 403 `csrf_failed`, and a foreign Origin returned 403.

**Building behind a TLS-intercepting proxy.** This sandbox's egress proxy re-signs HTTPS traffic. Without the proxy's
CA, pip and npm fail inside the build containers with a certificate error. Both Dockerfiles accept extra trust
anchors: place PEM files named `*.crt` in `services/api/extra-ca/` and `apps/web/extra-ca/` (see the README
there). The build appends them to the image's bundle. The directories hold no certificates by default, and
certificate verification was not disabled. On an ordinary network no extra CA is needed.

Compose publishes PostgreSQL on `127.0.0.1:${POSTGRES_HOST_PORT:-5432}`. Set `POSTGRES_HOST_PORT` if another
PostgreSQL already listens on 5432.

## Desktop agent

Development (any OS, simulated actions only):
```sh
cd apps/desktop
ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm install
npm run headless -- --server http://127.0.0.1:8000 --code XXXX-XXXX --platform simulated --data-dir /tmp/jarvis-agent
```
`--platform simulated` never touches the real computer; results read `SIMULATED: ...`.

Windows (not verified here; Electron could not be launched in this environment):
```powershell
cd apps\desktop
npm install
npm start            # Electron window: enter the server URL and the pairing code
```
The agent stores its secret with Electron `safeStorage` and an allowlist in `%APPDATA%\JARVIS\agent.json`.
It only launches applications, URLs, folders, projects, and files that are listed there.

## Tests

```sh
# Backend: unit, integration and backup-script tests (PostgreSQL; JARVIS_TEST_DATABASE_URL names the test database)
cd services/api && .venv/bin/python -m pytest -q -p no:cacheprovider

# Desktop agent core (typecheck, tests, bundle build)
cd apps/desktop && npx tsc -p tsconfig.json --noEmit && npx vitest run && npm run build

# Web app (typecheck, tests, production build)
cd apps/web && npm run typecheck && npx vitest run && npm run build

# Milestone end to end: its own API on a fresh database, fake Gemini, and the built web app
cd apps/web && npx playwright test milestone.spec.ts --config ../../tests/e2e/playwright.config.ts

# Accessibility scan (axe-core). Run it as a separate invocation: see the note below.
cd apps/web && npx playwright test a11y.spec.ts --config ../../tests/e2e/playwright.config.ts
```

**Run the two Playwright specs separately.** Each invocation recreates `jarvis_e2e` and starts one API. The
accessibility spec creates the owner account first, so a combined run leaves the milestone's setup page
unreachable and the milestone fails at its first step. Run alone, each passes. This was seen in the 2026-10-21 run.

Results from the phase 3 verification run on 2026-10-21 (measured on the final tree):

- Backend `pytest -q` on a fresh copy of `jarvis_vector_template`: **1103 passed, 1 skipped, 0 failed.** The
  migration test needs `vector` installed in `jarvis_vector_template` (see setup below). `alembic heads` shows one head,
  `d7a09bd3dc82`.
- Backup and restore (inside the backend run, plus a manual run against scratch databases): a dump, its checksum,
  restore into an empty database, refusal without `--force` into a populated one, `--force` restore, and `alembic check`
  after restore. Every step exited as expected. The password is passed to `pg_dump` through `PG*` variables, not argv.
- Desktop: `npx tsc --noEmit` clean; `npx vitest run` **227 passed** (11 files); `npm run build` succeeded.
- Web: `npm run typecheck` clean; `npx vitest run` **179 passed** (26 files); `npm run build` succeeded.
- CI: `.github/workflows/ci.yml` parses as YAML; four jobs (`backend`, `desktop`, `web`, `e2e`) each set `runs-on` and
  steps. The workflow itself was not run on GitHub.
- Playwright milestone (run alone): **1 passed** (owner, Gemini key, pairing, approval, real result, overview).
- Playwright accessibility scan (run alone): **1 passed**. The scan rendered 21 route and viewport combinations with
  **zero violations of any impact**. The scan does not open dialogs or error states.

Results from the phase 4 run (features after phase 3, listed in [docs/phase4/features.md](docs/phase4/features.md)),
measured on the final tree:

- Backend `pytest -q`: **1165 passed, 1 skipped, 0 failed.** No backend source changed after that run.
- Web: `npm run typecheck` clean; `npx vitest run` **294 passed** (47 files); `npm run build` succeeded.
- Desktop: no desktop code changed in phase 4, so the phase 3 result (227 passed, 11 files) stands; not re-run in phase 4.
- Playwright milestone (run alone): **1 passed**.
- Playwright accessibility scan (run alone): **1 passed**, 21 route and viewport combinations, **zero violations**.
- Nothing in phase 4 was run against live Gemini, Google or a real browser. Browser-only behaviour is listed under
  Known limitations.

Results from the phase 5 run (quick window, "Hey JARVIS", portable Windows build; see [docs/phase5/features.md](docs/phase5/features.md)):

- Web: `npm run typecheck` clean; `npx vitest run` **333 passed** (51 files); `npm run build` succeeded.
- Desktop: `npx tsc -p tsconfig.json --noEmit` clean; `npx vitest run` **248 passed** (12 files); `npm run build` succeeded.
  Portable zip built with `electron-builder --win zip --x64`: it contains `JARVIS Agent.exe`. Not run on Windows.
- Backend: no backend source changed in phase 5.
- Playwright, each run alone: the milestone **1 passed**; the accessibility scan **1 passed**, 23 route and viewport
  combinations including the quick window, **zero violations**; the quick-window spec **1 passed** (a reply in the popup,
  an approved action from the popup, and "Hey JARVIS" with a fake speech engine opening the quick page).
- Not verified: a real microphone, the real speech services in Chrome, Edge or Electron, the Ctrl+Space key press itself
  (the popup window was driven directly under a virtual display), and the NSIS installer (needs Windows or Wine).

Test database setup (once per PostgreSQL cluster, as a superuser):
```sh
createdb -O jarvis jarvis_vector_template
psql -d jarvis_vector_template -c 'CREATE EXTENSION IF NOT EXISTS vector;'   # as postgres
```

## Status

Verification levels: **Verified** means a test or a live check in this repository exercised the feature, and the
note says which. **Verified against mocks** means the test replaces the outside service (provider, OAuth provider,
browser API, desktop platform). **Not verified** means nothing in this sandbox exercised it.

### Phase 3 features

| Feature | Level | Evidence |
|---|---|---|
| Member accounts managed by the owner | Verified | Integration tests for every `/users` route, member isolation, deactivation and reset. Live stack: member gets 403 on `/users`, sees no devices. Web: Users tab for the owner only, mocked API. |
| Conversation export (Markdown, JSON) | Verified | Integration tests; live stack returns an attachment with `no-store`; a member gets 404. Web download through a Blob URL, mocked fetch. |
| Settings that change behaviour: notification filter, automatic summaries, task timeout | Verified against mocks | Backend tests for the inbox filter, the summary rules (the provider is a fake) and the timeout sweep. Summaries have not been run against live Gemini. |
| Prometheus metrics with a bearer token | Verified | Tests; live stack: 404 unset, 401 without or with a wrong token, 200 with the right one. |
| Developer logs and config check: owner only | Verified | Integration tests; members get 403. |
| Approved project checks (`run_check`) on the desktop agent | Verified against the simulated platform | Desktop tests, `shell: false`, output cap, kill of the process tree (simulated). Backend policy always requires approval. Real Windows processes not run. |
| Gmail, Google Drive and Notion (read-only) | Verified against mocks | OAuth flow, token handling, tools and the connection test tested with mocked HTTP. No live Google or Notion call. Notion PKCE is not verified. |
| Daily briefing plugin | Verified against mocks | Integration tests. Calendar part uses mocked HTTP. Scheduler firing a briefing end to end is not tested. |
| Browser alerts, deep links, Summary badge, hidden-notifications note | Verified against mocks | Vitest with a mocked `Notification`, mocked fetch and jsdom. Not run in a real browser against a live backend. |
| Voice settings applied (push to talk, interruptions, continuous listening) | Verified against mocks | Mode rule tested. Speech engines are not available in the sandbox. |
| Backup and restore scripts | Verified | Tests and manual runs against scratch databases: checksum, refusal, `--force`, `alembic check`. Off-host copies are not handled by the script. |
| Docker Compose stack | Verified (this sandbox) | Built, started, smoke-tested through the proxy, torn down. Not run on other hosts. |
| Accessibility scan (axe-core, WCAG 2.x A/AA) | Verified | 21 route and viewport combinations, zero violations. Open dialogs and error states are not scanned. |
| CI workflow | Verified (structure only) | YAML parses; jobs and steps present. Not run on GitHub. |

### Phase 5 features

| Feature | Level | Evidence |
|---|---|---|
| Quick page: request, reply, approval inside the popup | Verified | Playwright (`tests/e2e/quick.spec.ts`) against the real backend, fake Gemini and the built app; screenshot checked at 640×430. |
| "Hey JARVIS" opens the quick page with the request | Verified against a fake speech engine | The same Playwright spec; unit tests for the phrase rules and the listener. Real microphone not tested. |
| Quick window in the desktop app (Ctrl+Space, tray, hide on Esc or blur, origin rules) | Verified under a virtual display | Electron 44 under Xvfb: show, hide, the page's `hide()`, navigation and `window.open` refused. The key press itself was not simulated. |
| Hotkey, origin, centring and microphone permission rules | Verified | Desktop unit tests (`test/quick.test.ts`). |
| Portable Windows zip | Built, not run | `JARVIS Agent-1.0.0-x64.zip` from `electron-builder --win zip --x64`. Not started on Windows. |
| Microphone inside the desktop popup | Not verified | Electron has no speech service keys by default. Typing works. |

### Phase 4 features

| Feature | Level | Evidence |
|---|---|---|
| Task list, audit trail and conversation CSV exports | Verified | Integration tests (headers, columns, formula cells, filters, scope). Web downloads through a mocked fetch. |
| Download my data (account export) | Verified | Integration tests, including a scan of every key for secrets and a planted provider key that must not appear. |
| Clear old conversations (count first, pinned kept) | Verified | Integration tests, including audit without titles and member scope. Web tests for the typed confirmation. |
| Thumbs ratings and pinned conversations | Verified | Integration tests and a migration test for `e1f4b8c2a9d3`. Web tests with mocked fetch. |
| Global search across conversations, memories and tasks | Verified | Integration tests (wildcards, length limits, expiry, member isolation). Palette results tested with mocked fetch. |
| Audit date range (`since`, `until`) | Verified | Integration tests on exact boundaries, UTC for zone-less times, `invalid_range`. Activity date fields tested with mocked fetch. |
| Daily AI call limit and the calls-today line | Verified | Integration tests with the chat fixture: no provider call once the limit is reached, one warning per day, the count matches the limit check. |
| Quiet hours for browser pop-ups | Verified against mocks | Integration tests for the settings rules; web tests for the window and the alert gate. Pop-ups not seen in a real browser. |
| Next runs preview on the schedule form | Verified | Integration tests (daily, weekly, DST gap, past one-time spec); web tests for the spec builder. |
| Command palette, recent conversations and settings shortcuts | Verified against mocks | Web tests for the filter, keyboard rules and the component. Not driven in a real browser. |
| High-contrast theme | Verified against mocks | Settings schema tests; contrast computed from the hex values (at least 13:1 for text). Not seen in a browser. |
| First-run checklist, keyboard shortcuts in Help | Verified against mocks | Web tests for each step's rule and dismissal. |
| Find in a conversation | Verified against mocks | Web tests. Searches the 200 most recent messages the page loads. |
| Read reply aloud (saved voice, speed and pitch) | Verified against mocks | Web tests with a fake speech engine. Speech engines are not available in the sandbox. |
| Unsent chat drafts kept per conversation, cleared on sign out | Verified against mocks | Web tests with jsdom localStorage, blocked storage and sign out. |
| Following a streaming reply, with Jump to latest | Verified against mocks | Web tests with mocked layout. Not checked with a long conversation in a real browser. |
| Activity CSV and Run again for finished tasks | Verified against mocks | Web tests: request routes, statuses that offer Run again, the body sent. |
| Accessibility scan after phase 4 changes | Verified | Playwright, 21 route and viewport combinations, zero violations. |

### Phase 2 and milestone features

| Step | Status | Evidence |
|---|---|---|
| 1. Application launches | Verified | `scripts/dev-backend.sh`; Playwright starts the API and serves the built UI |
| 2. Dashboard loads | Verified | Playwright opens Overview and reads the completed task |
| 3. Gemini key stored securely | Verified against a fake Gemini | Key is encrypted at rest (test); never echoed to the page (Playwright) |
| 4. Provider connection test | Verified against a fake Gemini | Playwright "Test connection" passes; live Google API not reachable from this environment |
| 5. AI conversation | Verified against a fake model | Streaming chat tested; live model output quality not verified |
| 6. Conversation appears in the UI | Verified | Playwright sees the proposal card and chat text |
| 7. Pair the desktop agent | Verified with the simulated agent | Pairing and WebSocket over a live server; headless agent paired and `online` against the compose API (2026-10-21) and a host-run API (2026-10-20); no Windows agent run |
| 8. "Open Chrome on my laptop" | Verified | Playwright types the request |
| 9. Correct device identified | Verified | Device-resolution tests |
| 10. Action validated | Verified | Policy and argument validation tests |
| 11. Approval granted | Verified | Playwright clicks Approve; the command is not sent before approval (test) |
| 12. Desktop agent opens Chrome | **Not verified** | Only the simulated platform ran. The real Windows Chrome launch was never run |
| 13. Real result returned | Partly verified | The result travels the real protocol; the content is simulated |
| 14. Dashboard shows the outcome | Verified | Playwright reads "Completed" on Overview |
| 15. Errors reported honestly | Verified | Tests for offline device, revoked agent, forged result, pre-pairing refusal |

## Known limitations

- **Live providers were not reached.** This environment cannot reach Google, Notion, GitHub or Hugging Face. Gemini,
  Gmail, Drive, Notion, Calendar and GitHub behaviour is tested against mocks and a fake Gemini server.
- **Real Chrome, VS Code, and Explorer launches on Windows are unverified.** The Windows adapter is covered by tests with fake processes, and so is the process-tree kill for project checks.
- **Electron was not started.** The window, CSP, tray, and `safeStorage` are covered only by type checks and unit tests of the modules they call.
- **Replay protection is persisted, with limits.** The desktop agent keeps `replay-store.json` in its data directory and
  writes it before each command runs. If the file is corrupt, or was deleted on a paired device, the agent refuses
  commands issued before it started and keeps that floor for one replay window (600 s). Owner-only file modes are not
  enforced on Windows. Two agent processes sharing one data directory overwrite each other's records. Command lifetime
  is capped at 540 s.
- **A late success after a timeout** completes the task and is kept in the timeline and the chat, with no new
  notification. A scheduled run that already failed on timeout stays failed. If a device never confirms a cancel, the
  action may still finish; the task stays cancelled and the follow-up message says so. A timed-out task is not sent a
  stop, so the device may keep working on it.
- **Browser alerts need an open page.** They are shown only while the tab is hidden and the page is loaded. There is no
  service worker and no Web Push, so a closed browser gets nothing.
- **Voice behaviour depends on the browser.** Interruptions need the browser to report speech start during playback, and
  some browsers do not. Wake word is not available. Only jsdom tests cover the voice code.
- **Member accounts.** There is no invitation email and no email verification. The owner sets each member's first
  password and shares it out of band. Members can be deactivated, not deleted. Roles cannot be changed through the API.
- **Automatic summaries** cost one provider call each. They run at most once per conversation per process, so several
  backend processes can summarise the same conversation at once. Only the newest 200 messages in the window are read.
- **The developer log buffer is process-wide.** It is readable by the owner only, but it does not record which user a line
  belongs to.
- **The daily briefing** is built from fixed text templates, not a model. Its calendar part covers the next 24 hours at most 10
  events. A scheduled briefing keeps its full text in the task output, and the completion notification does not include it.
- **Plugins run in-process with full privileges and are not sandboxed.** Three in-repo plugins, `clock`, `briefing` and
  `calculator`, are enabled by default through `ENABLED_PLUGINS`. Only reviewed code belongs in `app/plugins/`. See [docs/plugins.md](docs/plugins.md).
- **Integrations.** Google Calendar and GitHub have OAuth connect flows and read tools. Gmail, Google Drive and Notion have
  OAuth connect flows and read-only tools (`gmail_recent_messages`, `drive_search_files`, `notion_search`). Google apps in
  *Testing* status expire refresh tokens after about seven days. Notion grants no scope list and disconnect does not
  revoke the token at Notion. Discord and Slack remain planned.
- **Semantic memory search** is implemented behind `GEMINI_EMBEDDING_MODEL` (unset means keyword search only). It has
  not been run with live Gemini embeddings, so ranking quality is unverified. It has no relevance cutoff and no vector
  index. Memories saved by the `save_memory` tool are not embedded when saved; a reindex covers them.
- **The Playwright specs must run as separate invocations** (see Tests). A combined run fails the milestone.
- **Phase 4 browser behaviour is tested with mocks only.** Find, read aloud, drafts, Jump to latest, the palette's recent list,
  quiet-hour pop-ups and the Activity and Run again actions have web tests, but nothing drove them in a real browser. Read
  aloud depends on the browser's speech engine; some browsers send the text to their own online voices. Drafts live in
  this browser's localStorage and are not shared between devices.
- **The daily call limit counts provider calls, not tokens.** The usage table does not store token counts, so there is no
  token budget.
- **Phase 5 voice is browser-only.** "Hey JARVIS" needs a JARVIS page open in Chrome or Edge. The desktop popup's microphone
  depends on Electron's speech support, which is unverified. The NSIS installer must be built on Windows (`npm run dist`).
- **The iPad companion is not built.**
- **Settings are per user, not per device.** The reminder timezone is a per-user setting; the chat uses UTC until it is set.
- **Security scanning was not run.** HawkScan needs its CLI and a `HAWK_API_KEY`. Docker is available, but the key is not.
- **Local runs need `JARVIS_ENV=development`.** The default is `production` (secure cookies, required encryption key,
  API docs disabled). `.env.example` sets `development`.

## Where to read more

- [docs/api.md](docs/api.md): REST and SSE contract
- [docs/desktop-protocol.md](docs/desktop-protocol.md): WebSocket protocol and command signing
- [docs/architecture.md](docs/architecture.md): modules, policy rules, and service contracts
- [docs/operations.md](docs/operations.md): backups, restore (connection passed through PG* variables), monitoring, and key handling
- [docs/phase2/](docs/phase2/): contracts for the phase-2 features (late results, integrations, plugins, memory, desktop replay store, web)
- [docs/phase3/](docs/phase3/): users, settings and export, metrics, project checks, integrations, briefing, web app
- [apps/ipad-companion/README.md](apps/ipad-companion/README.md): iPad companion design and platform limits
