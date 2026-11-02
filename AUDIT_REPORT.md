# JARVIS AI Core audit report

Audit date: 2026-11-02

Audited revision: `baec736536910eab9bb4f5de00d2b263c4b4c8fa` plus the changes documented in `FIXES_IMPLEMENTED.md`

Release target: `1.1.0-rc.1`

## Audit checklist

- [x] Repository structure, documentation, history, branch, tags, remotes, lockfiles, CI and release workflows inspected.
- [x] Backend, web, Electron desktop and Android source and tests inspected.
- [x] Backend, web and desktop dependencies installed from their pinned manifests.
- [x] Backend, web and desktop baseline suites executed locally.
- [x] Docker images built and an authenticated first-run smoke journey executed.
- [x] Production dependency vulnerability scans executed for Python and npm.
- [x] Windows portable ZIP built and checksummed.
- [x] Android debug APK rebuilt in an isolated Android 35 SDK image; unit tests, assembly and APK signature verification passed.
- [ ] Native Windows runtime and NSIS installer executed on Windows. This environment is Linux.
- [ ] Live AI, OAuth, ElevenLabs or MCP calls. No required credentials or external services are configured.
- [ ] GitHub Actions and release publication. Workflows were inspected locally; no tag or release was published.

## Confirmed findings

### AUD-001 — Critical — release workflow could publish after Android failure — fixed

Location: `.github/workflows/release.yml`, former `github-release` job condition.

Reproduction: inspect the release condition on the audited revision. It accepted `android-debug.result == failure` as long as the Windows job succeeded, allowing an incomplete release to publish.

Root cause: Android was treated as optional even though the workflow and release contract describe both packages as required.

Fix: release publication now requires both platform jobs to succeed. The workflow also generates and publishes `SHA256SUMS.txt`.

### AUD-002 — High — cloud Docker builds failed TLS verification — fixed

Locations: `services/api/Dockerfile:19`, `apps/web/Dockerfile:8`.

Reproduction: `docker compose up --build -d` failed while pip fetched `setuptools`, with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`.

Root cause: the Dockerfiles expected users to copy a proxy certificate into the build context. The managed environment provides a CA path, but inner builds do not inherit that trust store. Copying a session CA would also retain it in an image layer.

Fix: pip and npm dependency steps accept an optional BuildKit `proxy_ca` secret. TLS verification stays enabled and the CA is not copied into the image.

### AUD-003 — High — API container could not start as its unprivileged user — fixed

Location: `services/api/Dockerfile:13`.

Reproduction: after a successful image build, the API container restarted with `PermissionError: pyproject.toml` while Alembic initialized.

Root cause: this snapshot stores checkout files with owner-only permissions. Docker copied them as root, then ran Alembic as UID 10001.

Fix: runtime files are copied with `--chown=jarvis:jarvis`. The rebuilt API became healthy and migrations completed.

### AUD-004 — High — “70+ native tools” is not implemented

Locations: `services/api/app/tools/catalog.py:87`, `services/api/app/tools/registry.py:14`.

Evidence: loading the actual registry produced 23 tools total. Eight target a paired device: `open_url`, `launch_app`, `open_folder`, `open_project`, `system_info`, `read_file`, `app_availability` and `run_project_check`. The remaining 15 are server tools.

Impact: marketing or release notes must not claim 70+ working native tools. Clipboard, screenshots, general file editing/search, window management, notifications and arbitrary terminal operations are not present as registered native actions.

Proposed next step: add narrowly scoped actions with local allowlists, argument schemas, approvals, platform implementations and tests; report the registry count from code.

### AUD-005 — High — MCP is absent

Evidence: no MCP transport, discovery lifecycle, schema import, permission manifest, execution path or connection-status UI exists in application source. The existing in-process plugin loader is not MCP.

Impact: MCP configuration, discovery and execution requirements are not implemented.

Proposed next step: add a server-side MCP client manager, per-server allowlist, schema validation, explicit approval policy, lifecycle health and an integrations UI. Unknown tools must default to denied.

### AUD-006 — High — Android streaming cannot be cancelled

Locations: `apps/android/app/src/main/java/com/jarvis/ai/JarvisApi.kt:56`, `apps/android/app/src/main/java/com/jarvis/ai/JarvisApp.kt:161`.

Reproduction: start a long chat response. `sendMessage` owns a blocking `HttpURLConnection` until the stream ends and exposes no cancellation handle. The Compose screen shows a busy indicator and disables input, but has no Stop action.

Root cause: the streaming API is synchronous and the connection is local to the function.

Impact: the historical Android Stop/cancellation issue remains confirmed. A late stream can continue updating UI state until disconnect or timeout.

Proposed next step: return a cancellable request object, disconnect on coroutine cancellation, add a Stop control and regression tests for stale deltas.

### AUD-007 — High — flagship features are not implemented

Ghost Mode, Parallel Self, Rescue Mode, Memory Graph, Future Simulator, Cross-Device Continuity and Skill Forge have no end-to-end implementation or UI. Existing tasks, memories and plugins provide useful foundations but do not meet those feature contracts.

Proposed dependency order: approval/audit primitives → durable task graph → workflow recorder → multi-agent executor → rescue plans/rollback → graph provenance → scenario comparison → device handoff → generated-skill quarantine and activation.

### AUD-008 — High — ElevenLabs is not integrated

Location: `services/api/app/schemas/settings.py:65` defines browser-oriented voice settings only. No ElevenLabs API client, secret binding, proxy endpoint, model capability discovery, audio stream or UI provider selection exists.

Impact: live ElevenLabs STT/TTS, latency, interruption and multilingual behavior are unverified and unavailable.

Blockers: no ElevenLabs credential is configured, and the managed network does not currently allow `api.elevenlabs.io` or the official documentation domain. See `ELEVENLABS_SETUP.md`.

### AUD-009 — Medium — direct provider support is narrower than requested

Location: `services/api/app/providers/registry.py:43`.

Evidence: the catalog contains Gemini, Hugging Face Router and OpenRouter. There are no direct OpenAI, Anthropic or Ollama adapters. OpenRouter may expose models from other vendors, but that is not equivalent to their direct APIs.

Impact: provider-specific capabilities, local Ollama, routing cost policies and direct credential behavior are absent.

### AUD-010 — Medium — Android package is debug-only and unsigned for distribution

Locations: `.github/workflows/release.yml:13`, `apps/android/app/build.gradle.kts:18`.

Evidence: the release workflow builds and uploads `app-debug.apk`; there is no release signing configuration. A debug APK is useful for testing but is not a Play-ready production artifact.

### AUD-011 — Medium — Windows binary is unsigned and not executed on Windows here

Location: `apps/desktop/electron-builder.yml:14` explicitly disables executable signing/editing.

Evidence: a Windows x64 ZIP was built on Linux and its archive contents were checked. The executable and NSIS installer were not run on Windows.

### AUD-012 — Medium — backup/restore integration checks need host PostgreSQL clients

Evidence: six backend tests were skipped because `pg_dump`, `pg_restore` and `psql` are not installed on the host. PostgreSQL 16 with pgvector runs in Docker, but the scripts deliberately invoke local client binaries.

Impact: the scripts remain covered by historical tests but were not revalidated in this environment. Client/server compatibility is therefore not claimed for this candidate.

### AUD-013 — Medium — server file browsing/editing is explicitly incomplete

Location: `apps/web/src/pages/files/FilesPage.tsx:53` and `:70`.

Evidence: the page states that server browsing/editing is not implemented. The desktop `read_file` action is limited to pre-approved files and 64 KB.

### AUD-014 — Low — checked-in presentation and status claims were stale

Evidence: `docs/submission-checklist.md` claimed generated APK and Windows outputs before this audit, and package metadata stayed at `1.0.0` while remote tags reached `v1.0.6`.

Fix: package versions are synchronized to `1.1.0-rc.1`; this report and `FEATURE_STATUS.md` replace unsupported completion claims for the candidate.

### AUD-015 — Low — placeholder brand artwork remains

Location: `scripts/gen-icons.py:3` describes the generated PWA artwork as placeholder art.

Impact: functional but not final product branding.

## Historical issue revalidation

| Historical area | Result in this audit |
|---|---|
| Electron startup/tray/quick window | Core logic verified by 248 tests and bundle build; native Windows runtime not available. |
| Web Chat/Quick Ask cancellation | Existing web regression tests passed; browser E2E was not rerun. |
| Android Memory/navigation | Source and tests inspected; APK/instrumented tests blocked by SDK license. |
| Android sessions/cookies/redirects | Cookie/CSRF implementation exists; unit tests cover parsing and task actions. Device runtime not verified. |
| Android streaming/Stop | Confirmed missing cancellation path; see AUD-006. |
| MCP | Confirmed absent; see AUD-005. |
| PostgreSQL backup/restore | Six operational tests skipped; see AUD-012. |
| Docker/API startup | Two reproduced defects fixed; authenticated smoke journey passed. |
| Windows installer/PowerShell | Portable ZIP built; Windows installer and PowerShell execution not verified here. |
| Android Gradle/APK signing | Clean unit-test/build passed in an isolated Android 35 SDK image. A v2 debug-signed APK was produced; production signing is absent. |
| CI/Playwright/release | Workflow inspected and partial-release bug fixed. Actions and Playwright were not run in GitHub/browser. |

## Release decision

`1.1.0-rc.1` is a development release candidate for the verified backend/web/desktop core and the new text EQ slice. It is not a complete implementation of the master roadmap and is not ready to be called a production multi-platform release until Android, Windows-native, backup/restore and external-service checks are completed.
