# Feature status for 1.1.0-rc.1

Status labels describe this candidate, not historical claims.

| Feature | Status | Evidence and limitation |
|---|---|---|
| Backend/auth/database | Verified | Full automated suite and authenticated Docker smoke journey; six external backup-tool checks skipped. |
| Web command centre | Verified | TypeScript, 333 tests and production build; browser Playwright suite not rerun in this environment. |
| Electron desktop core | Implemented but not fully verified | 248 tests and Windows ZIP cross-build; no native Windows execution. |
| Android companion | Partial | Clean unit tests and debug APK build passed; v2 debug signature and package metadata verified. Streaming still has no Stop path and production signing is absent. |
| AI providers | Partial | Gemini, Hugging Face Router and OpenRouter exist. Direct OpenAI, Anthropic and Ollama adapters do not. Live calls need keys. |
| Streaming/context/fallback/tools/usage | Verified locally | Backend integration tests cover these paths with fake providers. Live provider behavior is unverified. |
| Native tools | Partial | 23 registered tools total; eight paired-device tools. The requested 70+ claim is unsupported. |
| Existing memory | Implemented but not fully verified | User-isolated CRUD, semantic retrieval and export exist; live embedding provider behavior was not exercised. |
| Emotional adaptation | Implemented but not fully verified | Text/context engine, correction behavior, opt-out and ephemeral prompt policy are tested. No voice cues or user-consented retention. |
| ElevenLabs | Not implemented | No client, secret binding, stream, provider UI or live test. Existing browser voice remains the fallback. |
| MCP | Not implemented | No MCP transport, discovery, permission or execution layer. |
| Ghost Mode | Not implemented | No recorder, workflow editor or replay engine. |
| Parallel Self | Not implemented | Existing task workers are not a specialist multi-agent dependency engine. |
| Rescue Mode | Not implemented | Project checks are narrower than diagnosis, repair, backup and rollback. |
| Memory Graph | Not implemented | Current memory is a list/vector store without graph entities, edges or conflict resolution. |
| Future Simulator | Not implemented | No structured scenario comparison system. |
| Cross-Device Continuity | Partial | Web/device tasks and Android access exist, but checkpoint handoff, conflict resolution and offline continuation do not. |
| Skill Forge | Not implemented | In-process plugins exist; generated skills, quarantine, approval, rollback and history do not. |
| OAuth integrations | Implemented but not fully verified | GitHub, Google and Notion flows have automated coverage; live OAuth needs credentials and callbacks. |
| Scheduling/notifications/task approvals | Verified locally | Backend and web automated coverage passed. OS notification behavior remains platform dependent. |
| Release workflow | Implemented but not fully verified | Static workflow corrected; GitHub Actions/tag publication was not run. |

## Major feature evidence

### Emotional adaptation

- Files: `services/api/app/services/emotion.py`, `services/api/app/services/chat.py`, `services/api/app/schemas/settings.py`, `apps/web/src/pages/settings/AiSection.tsx`.
- Tests: emotion unit tests, prompt composition tests, schema defaults, web settings tests, and the full chat integration regression.
- Remaining work: optional consented speech analysis, voice-expression mapping, persisted feedback only after a separate retention opt-in, and live model evaluation.

### Desktop agent

- Files: existing `apps/desktop/src` implementation; version constants updated for this candidate.
- Tests: 248 automated tests, TypeScript compilation, application bundle, and Windows x64 ZIP cross-build.
- Remaining work: execute startup, tray, global shortcut, voice devices, installer and shutdown on Windows 10/11; configure signing.

### Android

- Files inspected: `JarvisApi.kt`, `JarvisApp.kt`, navigation and Gradle configuration.
- Package: `release/JARVIS-Android-1.1.0-rc.1-debug.apk`, version code 7, min SDK 26, target SDK 35.
- Remaining work: make the stream call cancellable, expose Stop, suppress stale deltas, add lifecycle tests, configure production signing and test on a physical device.

### Flagship roadmap

No roadmap feature is presented as complete. The safe implementation order is approvals/audit primitives, durable task graph, Ghost Mode, controlled agent execution, Rescue Mode, provenance graph, simulation, handoff, then generated-skill quarantine and activation.
