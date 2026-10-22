# Phase 5: ask from anywhere (quick window, "Hey JARVIS", portable Windows build)

Phase 5 makes JARVIS usable every day without opening the web app first. It adds three things:

| Feature | Where it lives | How you use it |
|---|---|---|
| Quick window | Desktop agent (`apps/desktop`): a small frameless window showing the web app's `/quick` page | Press **Ctrl+Space** (or **Quick ask** in the tray menu) |
| "Hey JARVIS" | Web app: a background listener in the app shell, and the quick page | Settings → Voice → **Hey JARVIS**, then say the phrase in Chrome or Edge |
| Portable Windows build | Desktop agent packaging | `apps/desktop/release/JARVIS Agent-1.0.0-x64.zip` (built here), or `npm run dist` on Windows for the installer |

The step-by-step guide is [docs/TUTORIAL.md](../TUTORIAL.md), Part 5.

## Verification levels

| Feature | Level | What exercised it |
|---|---|---|
| Quick page: type, reply, approve inside the popup, result on the dashboard | **Verified** | Playwright (`tests/e2e/quick.spec.ts`) against the real backend, a fake Gemini and the built web app, at the popup's 640×430 size. Screenshot checked. |
| "Hey JARVIS" from another page opens the quick page and sends the request | **Verified against a fake speech engine** | The same Playwright spec, with a fake `SpeechRecognition` that delivers a final result. |
| Wake-word rules (phrase, request capture, follow-up window, false triggers) | **Verified** | Unit tests: `apps/web/src/__tests__/web-phase5-wakeword.test.ts`. |
| Listener behaviour: blocked microphone, unreachable speech service, restart after each utterance, stop after repeated failures, cleanup | **Verified against a fake engine** | `web-phase5-wakeword-hook.test.tsx`. |
| Quick page reducers (streamed text, final message, proposals, errors) and titles | **Verified** | `web-phase5-quickask.test.ts`. |
| Popup window in the real Electron runtime: hotkey registration, show and hide, `hide()` from the page, navigation off the web origin refused, `window.open` refused | **Verified under a virtual display** | A harness in Electron 44 under Xvfb that loads the compiled popup module. The key press itself was not simulated, because no X key tool is installed here. |
| Hotkey and tray rules: shortcut validation, fallback and warning, centring on screen, origin checks, microphone permission only for the popup origin | **Verified** | `apps/desktop/test/quick.test.ts` (21 tests). |
| Desktop main process starts and keeps running | **Verified under Xvfb** | 40-second smoke run with the simulated platform; D-Bus messages only, no crash. |
| Portable Windows zip | **Built, not run** | `electron-builder --win zip --x64` on Linux. It contains `JARVIS Agent.exe` and `resources/app.asar`. Not started on Windows. |
| Windows NSIS installer | **Not built here** | The NSIS step runs `makensis` through Wine, which is not installed in this environment. Build it on Windows with `npm run dist`. |
| Microphone in the desktop popup | **Not verified** | Electron's build has no speech service keys by default. Chromium started the recognizer once the media permission was granted, and reported `audio-capture` with a fake device (no audio). Whether real speech returns text inside Electron is unknown. Typing always works. |
| Real microphone and speech service in Chrome or Edge | **Not verified** | No microphone in the sandbox. The browser path is covered by the fake engine tests only. |

## What changed

### Web app (`apps/web`)

- `/quick` route, outside the app shell and behind the sign-in gate. `pages/quick/QuickAskPage.tsx`.
  - One request in a box, Enter to send, Esc to close (the popup's `hide()` when the desktop bridge is present, otherwise
    it clears the box). Each request opens its own conversation titled with the request, so it stays findable in Chat.
  - A request handed over as `?ask=` is sent once, then the parameter is removed.
  - Replies stream in; approval cards (`ToolProposalCard`) appear in the same window.
- `services/wakeWord.ts`: the phrase rules. `hooks/useWakeWord.ts`: the listener. It restarts after each utterance, stops
  itself on a blocked microphone, an unreachable speech service, or three unexplained failures, and says why.
- `layouts/WakeWordListener.tsx`: mounted in the app shell. It is paused on `/voice` and `/quick`, which have their own
  microphone control. A small label in the bottom-left corner shows when it is listening. A request spoken after the phrase
  navigates to `/quick?ask=…`.
- Settings → Voice: the **Hey JARVIS** switch replaces the "not implemented" row. The setting itself already existed in
  the backend (`voice.wake_word_enabled`), so no backend change was needed.
- `services/speech.ts` now holds the recognition session that the voice hook used. Both the voice hook and the wake listener
  use it.

### Desktop agent (`apps/desktop`)

- `shared/quick.ts`: the popup's address (`JARVIS_WEB_URL`, default `http://127.0.0.1:5173`, https or loopback only), the
  hotkey (`JARVIS_HOTKEY`, default `Control+Space`, validated with a fallback and a warning), centring, origin checks and the
  microphone permission rule.
- `main/quick-window.ts`: the popup. Frameless, 640×430, always on top, skipped in the taskbar. It opens centred on the screen
  with the cursor, reloads its page each time, closes when it loses focus, and accepts `hide()` only from its own page.
  Navigation and new windows are refused outside the web app's origin (`guardWebContentsFor` in `main/window.ts`).
- `preload/quick.ts`: exposes `window.jarvisQuick.hide()` and nothing else. Bundled by `scripts/build-bundles.mjs`.
- `main/index.ts`: registers the hotkey; adds **Quick ask**, the shortcut's warning and **Start with Windows** (installed app
  only) to the tray menu; grants the microphone only to the popup's origin, and every other permission is still refused.

### Packaging

- `electron-builder.yml` is unchanged. The zip target is passed on the command line, because the NSIS target needs Wine on
  Linux.

## Known limits

- The hotkey is a key combination. A bare double-tap of Ctrl needs a low-level keyboard hook, which is not part of this build.
- `Control+Space` is also the input-method switch on some Windows layouts. Set `JARVIS_HOTKEY` if it clashes.
- "Hey JARVIS" needs a page of the web app open in Chrome or Edge. Closed tabs do not listen. Two open JARVIS tabs both listen.
- The speech engines send audio to the browser vendor's service. JARVIS does not store the audio.
- The popup needs the web app running, and it keeps its own sign-in.
