# Test results for 1.1.0-rc.1

Executed on 2026-11-02 in the managed Linux environment. Mocked, local integration and live external checks are distinguished below.

| Area | Command/check | Result | Scope |
|---|---|---|---|
| Backend baseline | `pytest -q` | 1,161 passed, 7 skipped, 2 warnings | Unit and PostgreSQL integration; before candidate changes. |
| Backend candidate | `cd services/api && .venv/bin/pytest -q` | 1,168 passed, 7 skipped, 2 warnings in 271.38s | Unit and PostgreSQL integration. |
| EQ targeted | emotion, prompt and settings tests | 86 passed | Unit/schema tests. |
| Web baseline | typecheck, Vitest, Vite build | 333 tests passed; build passed | jsdom/component and production compile. |
| Desktop baseline | typecheck, Vitest, build | 248 tests passed; build passed | Node/simulated platform. |
| Docker | API/web image builds | Passed | BuildKit with CA secret. |
| Docker runtime | health plus authenticated setup/settings | Passed | Local PostgreSQL/API/nginx; no external provider. |
| npm audit | web and desktop production dependencies | 0 vulnerabilities | Registry advisory database. |
| Python audit | `pip-audit` | 0 known vulnerabilities | Editable local package is not on PyPI and was skipped. |
| Android | clean unit tests and `assembleDebug` | Passed, 42 tasks | Containerized Android 35 SDK; debug package only. |
| Windows runtime | EXE/installer execution | Not run | Linux host. |
| Live AI/OAuth/voice/MCP | External calls | Not run | Credentials/services unavailable; MCP/ElevenLabs absent. |

Seven backend skips are explicit: six need host `pg_dump`, `pg_restore` and `psql`; one scheduling behavior is covered by the dedicated scheduling tests. The two warnings are SQLAlchemy inspection warnings for the PostgreSQL `vector` type.

The web and desktop suites use mocks for browser/OS boundaries. A successful cross-build does not establish native Windows behavior. No physical Android device or microphone/audio-quality evaluation was available.

## Candidate rerun

- Web: typecheck passed, 51 files/333 tests passed, and the `1.1.0-rc.1` production build passed.
- Desktop: typecheck passed, 12 files/248 tests passed, and the `1.1.0-rc.1` bundle build passed.
- Affected backend chat integration regression: 51 tests passed after the backward-compatibility correction.
- Windows ZIP: `unzip -t` reported no compressed-data errors and the archive contains `JARVIS Agent.exe` and its Electron runtime.
- Web archive: `tar -tzf` completed successfully.
- Android: clean `testDebugUnitTest assembleDebug` passed; APK v2 signature, package `com.jarvis.ai.debug`, version code 7, version `1.1.0-rc.1`, min SDK 26 and target SDK 35 verified.
- Windows portable EXE: fresh typecheck, 12 files/248 tests and bundle build passed; 7-Zip identified a valid NSIS archive and reported `Everything is Ok`.

Final backend candidate: 1,168 passed, 7 skipped, 2 warnings in 271.38 seconds. It was run from `services/api`, which loads the repository's pytest async and marker configuration.
