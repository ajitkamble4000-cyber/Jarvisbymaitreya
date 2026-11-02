# Fixes implemented for 1.1.0-rc.1

## Docker and startup

- Added optional BuildKit CA secrets to the API and web dependency stages. Corporate or managed proxy trust is available only while dependencies install and is not retained in an image layer.
- Changed API image copies to use the unprivileged `jarvis` owner. Alembic and Uvicorn can now read the application when source files in the checkout are owner-only.
- Rebuilt both images, started PostgreSQL/API/web, and completed health, owner-setup, session, CSRF and settings smoke checks through nginx.

Changed modules: `services/api/Dockerfile`, `apps/web/Dockerfile`, and both `extra-ca/README.md` files.

## Emotional adaptation

- Added a conservative text and context assessment with structured `intent`, `emotion_cue`, `confidence`, `source`, `response_style`, `user_confirmed` and `timestamp` fields.
- Added an independently controlled `ai.emotional_adaptation_enabled` setting, enabled by default.
- Added ephemeral response-style guidance to model prompts. Assessments are not written to messages, memories or settings.
- Added explicit correction handling, neutral behavior when evidence is insufficient, uncertainty language, and rules against diagnosis or manipulative phrasing.
- Added a web settings control and regression tests. Older settings documents and test doubles retain the enabled default.

Changed modules: `services/api/app/services/emotion.py`, `services/api/app/services/chat.py`, `services/api/app/schemas/settings.py`, `apps/web/src/pages/settings/AiSection.tsx`, and related types/tests.

This is a text-only first slice. Speech-signal analysis, retained emotion history and voice-expression planning are not implemented.

## Release engineering

- Synchronized candidate versions to `1.1.0-rc.1` (`1.1.0rc1` in Python package metadata).
- Prevented GitHub release publication when either the Windows or Android package job fails.
- Added SHA-256 manifest generation and publication to the release workflow.
- Preserved the existing rule that release publishing happens only from a version tag. No tag or GitHub release was created during this audit.

## Dependency hygiene

- Installed pinned backend, web and desktop dependencies.
- Upgraded the local virtual environment's pip from the only flagged version to 26.2.2.
- Verified zero known production npm vulnerabilities in the web and desktop lockfiles and zero known Python package vulnerabilities with `pip-audit`.

## Confirmed items left open

The following were reproduced or confirmed but require substantial product work or unavailable platforms: Android stream cancellation, Android release signing, native Windows execution/signing, MCP, ElevenLabs, direct OpenAI/Anthropic/Ollama adapters, backup/restore client compatibility, and the seven flagship roadmap features. Exact evidence and next steps are in `AUDIT_REPORT.md` and `FEATURE_STATUS.md`.
