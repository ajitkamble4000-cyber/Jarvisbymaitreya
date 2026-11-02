# JARVIS AI Core 1.1.0-rc.1

This development release candidate repairs Docker startup, adds a privacy-preserving text EQ slice, and tightens release packaging. It does not complete the full flagship roadmap.

## Added

- Conservative text/context emotion cues with structured intent, confidence, source and response style.
- Independent Emotional adaptation setting; assessments remain ephemeral and are not saved.
- BuildKit CA-secret support for managed-network API and web builds.
- SHA-256 manifest generation in the GitHub release workflow.

## Fixed

- API container startup when checkout files are owner-only and the runtime uses an unprivileged account.
- Release workflow no longer publishes when the Android package job fails.
- Candidate version metadata is aligned across API, web, desktop and Android.

## Packages

- `JARVIS Agent-1.1.0-rc.1-x64.exe` (100,190,175 bytes), SHA-256 `850b8dc822dfd3878209a16c9cb7558dcc3ef593a8148e67d507771d5f66ec99`: portable NSIS executable; archive integrity passed, unsigned and not run on Windows.
- `JARVIS Agent-1.1.0-rc.1-x64.zip` (154,530,811 bytes), SHA-256 `fa6c94fda0e28c7c69a5f3e8d37b581d22d6735ae8f5292c307aa2119c83bae2`: cross-built on Linux, archive-tested; unsigned and not run on Windows.
- `JARVIS-Android-1.1.0-rc.1-debug.apk` (21,010,315 bytes), SHA-256 `4c8a4103efe9e2ddac246848b45360f5030af49faab136c4174d75d52866071c`: clean unit-test/build passed; v2 debug signature and manifest metadata verified.
- `JARVIS-Web-1.1.0-rc.1.tar.gz` (1,301,492 bytes), SHA-256 `845bbe2f878b79975bec317533f54b161c7f9bf71767e409a1fba867f5e22461`: production static bundle, archive-tested.
- `SHA256SUMS.txt`: checksum manifest for both packages.
- Android APK: debug distribution artifact; physical-device behavior and production signing remain outstanding.

## Compatibility and known issues

- Configured direct AI providers remain Gemini, Hugging Face Router and OpenRouter.
- Android chat cannot cancel an active stream.
- MCP, ElevenLabs, Ghost Mode, Parallel Self, Rescue Mode, Memory Graph, Future Simulator and Skill Forge are not implemented.
- Cross-device support exists only in its current task/device form; durable handoff and conflict recovery are incomplete.
- Windows and Android signing are not configured.
- Six backup/restore tests require host PostgreSQL client tools and were skipped.

Read `AUDIT_REPORT.md`, `FEATURE_STATUS.md`, `TEST_RESULTS.md` and `SECURITY_REPORT.md` before evaluating or distributing this candidate.
