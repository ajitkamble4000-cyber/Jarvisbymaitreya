# iPad companion: design and platform plan

Status: **design only. Nothing is built.** The web app runs in iPad Safari today. A native iPad app is not started.

## What the companion would do

| Capability | Plan | Platform route |
|---|---|---|
| AI conversations and shared history | Same REST and SSE API as the web app | HTTPS to the backend, signed in with the same session |
| Voice | Native speech input and speech output | iPadOS speech frameworks, with a permission prompt |
| Device status and notifications | Read `GET /devices`, `GET /notifications` | Local notifications for approval requests |
| Approved links | Open URLs from the approved list | Universal Links or Safari, only links already approved on a desktop |
| Remote task requests | Create a task, then the user approves on the iPad | Normal task API; no iPad-side execution |
| Settings sync | `GET /settings` and `PATCH /settings` | Same settings document as the web app |
| Device pairing | Show the iPad as a device of the user | Pairing code flow; the iPad is not a desktop agent |
| Supported app launch | Shortcuts and app URL schemes, only where the target app publishes one | User runs the shortcut; JARVIS does not drive other apps |

## Platform limits (do not promise these)

- **No unrestricted control of the iPad.** iPadOS sandboxing does not allow a background service to open
  or script other apps. JARVIS cannot tap, type, or read other apps on the iPad.
- **Launching apps** works only through a URL scheme the target app documents, or through a Shortcut the user
  runs. Each use needs the user's action.
- **Background work is limited.** An iPad app cannot keep a socket open in the background. Approvals arrive as
  push notifications (needs an Apple push certificate and a server push path, not built) or when the app is opened.
- **No desktop agent.** The Windows agent protocol is not for iPad. The iPad sends requests; the desktop executes them.
- **Shared history** uses the same backend session. The iPad must sign in again when its session expires.

## Implementation requirements (not started)

1. An iOS or iPadOS app (SwiftUI) that uses the existing REST and SSE API. No new server endpoints are required for the basic flow.
2. Push notifications: an APNs key, a device-token registration endpoint, and a sender in the backend. None of these exist.
3. A Shortcuts integration (App Intents) for "ask JARVIS" and "approve the next task".
4. App Store review: privacy labels for microphone and notification use; no background execution claims.
5. Universal Links with an `apple-app-site-association` file on the web origin.

## Verification

Nothing here has been built or run. The web app's responsive layout was checked at 390 px width in the
Playwright milestone test, which covers phone-width browsers only.
