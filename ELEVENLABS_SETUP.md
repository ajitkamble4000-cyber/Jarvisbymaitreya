# ElevenLabs setup status

ElevenLabs is **not integrated in 1.1.0-rc.1**. The current product uses browser speech recognition and speech synthesis where the browser supports them. No API key should be added to frontend source or this repository.

## Required implementation

1. Add server-only secret configuration for an ElevenLabs API key and validate it without returning the key or upstream error bodies.
2. Query or pin models only after checking the current official API documentation. Do not invent model IDs, style fields, audio tags or conversational capabilities.
3. Add a modular voice-provider interface for TTS and, if supported and selected, STT. Keep browser voice as the fallback.
4. Proxy authenticated audio streams through the backend, with size limits, content-type validation, timeouts, retries and cancellation.
5. Add voice and output-device selection, visible recording state, interruptible playback, VAD policy, connection recovery and latency/error reporting.
6. Map the EQ response policy to supported voice controls only. Do not claim the assistant experiences emotions.
7. Add unit tests with a fake upstream, cancellation/network tests, and opt-in live tests. Evaluate real latency and audio quality with consented hardware.

## Configuration contract proposed for a future change

Use a server environment secret such as `ELEVENLABS_API_KEY`; never expose it through `VITE_*`, Android resources or Electron renderer code. Add non-secret voice/model preferences to the existing settings document only after capability discovery is implemented.

## Current blockers

- No ElevenLabs key is configured.
- The managed environment's network allowlist does not include the ElevenLabs API or official documentation domains.
- There is no microphone/speaker test device in this environment.

Because the API could not be researched live here, this candidate deliberately names no ElevenLabs model or unsupported request parameter.
