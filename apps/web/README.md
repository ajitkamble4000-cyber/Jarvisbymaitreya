# JARVIS AI web app

The command centre UI: React 19, TypeScript (strict), Vite 8, Tailwind CSS 4, React Router 7 and TanStack Query 5. It talks only to the REST and SSE contract in `docs/api.md`.

## Run it

```bash
cd apps/web
npm install
npm run dev          # http://localhost:5173, /api is proxied to http://127.0.0.1:8000
npm run typecheck    # tsc --noEmit
npm test             # vitest (jsdom)
npm run build        # tsc --noEmit, then vite build into dist/
```

The dev origin `http://localhost:5173` must be in the backend `ALLOWED_ORIGINS` so that CSRF checks pass. The Vite proxy keeps the browser `Origin` header (`changeOrigin: false`).

## How the app is organised

```
src/
  main.tsx, App.tsx         entry, QueryClient, ThemeProvider, toast host, router
  routes.tsx                the route table. Every page is lazy-loaded.
  layouts/                  AuthGate, AuthLayout, AppShell, nav model, sidebar customisation, route errors
  pages/<feature>/          one folder per page; helpers that are not components live in *Model.ts
  components/ui/            design system primitives (see below)
  components/orb/AIOrb.tsx  status orb; state mapping in orbVisual()
  components/markdown/      safe Markdown renderer (no raw HTML, safe links only)
  components/layout/        PageHeader
  providers/ThemeProvider   applies theme, accent, font size and reduced motion to <html>
  services/api.ts           typed client, CSRF, error envelope, SSE parser, events stream
  services/chatStream.ts    typed chat SSE events (send, regenerate)
  services/queries.ts       query keys (qk), one hook per endpoint, invalidationsForEvent()
  services/format.ts        dates, relative time, countdowns
  services/speech.ts        browser speech capability detection (no fake support)
  stores/ui-store.ts        theme, accent, reduced motion, orb, font, hidden nav, sidebar order
  stores/voice-store.ts     voice state machine (listening only after the engine starts)
  hooks/                    useAuth, useEventStream, useReducedMotion, useVoice, useAppearanceSync, ...
  types/api.ts              response types, field names identical to docs/api.md
  test/                     vitest setup and renderWithProviders()
  __tests__/                tests
```

### Route table

| Path | Page | Owner |
|---|---|---|
| `/setup`, `/login` | Owner setup, sign-in | WEB-A |
| `/overview` | Overview | WEB-A |
| `/chat/:conversationId?` | AI chat | WEB-A |
| `/voice` | Voice assistant | WEB-A |
| `/models` | AI models | WEB-A |
| `/devices` | Connected devices (`?pair=1` opens pairing) | WEB-A |
| `/tasks/:taskId?` | Tasks (`?create=1` opens the new task dialog, `?status=` is read by links) | WEB-A |
| `/schedule`, `/automation`, `/explore`, `/integrations`, `/memory`, `/files`, `/notifications`, `/activity`, `/analytics`, `/developer`, `/settings`, `/help` | Pages built by WEB-B | WEB-B (stubs until then) |

The optional `:id?` segments keep the chat and task views mounted when the selected item changes, so a streaming reply is not lost when a new conversation gets its URL.

`docs/architecture.md` does not list page paths, so the paths above are the ones this app defines. Add a page by adding a `page(() => import(...))` entry in `src/routes.tsx` and a `NavItem` in `src/layouts/nav.ts`.

### Auth gate

`AuthGate` calls `GET /auth/status` once and then routes the viewer: `setup_required` goes to `/setup`, a signed-out viewer goes to `/login` with the original path kept in router state, and a signed-in viewer on `/login` returns to that path (same-site paths only, see `safeReturnPath`). A 401 from any non-auth request re-checks the status.

### Shell

- Desktop: a sidebar with the navigation. "Customise" hides items and reorders them.
- Phone: a top bar with a menu button. The menu is a bottom sheet with the same navigation.
- The events stream opens once in `AppShell` (`useEventStream`). Each event invalidates the matching queries, so pages refetch real REST data. `approval.requested` also raises a toast.
- The skip link targets `#main-content`. Every page renders inside `<main id="main-content">`.

## Shared pieces for WEB-B

Import these instead of writing new ones.

**Page frame.** `PageHeader` from `components/layout/PageHeader` (title, description, actions, sets the document title). Wrap page content in `<div className="flex flex-col gap-6">`.

**Design system** (`components/ui`, also re-exported from `components/ui/index.ts`):

| Component | Notes |
|---|---|
| `Button` | variants `primary`, `secondary`, `ghost`, `danger`; sizes `sm`, `md`, `icon`, `xl`; `loading` sets `aria-busy` and disables |
| `IconButton` | `label` is required (it is the accessible name and tooltip) |
| `Card`, `CardTitle` | restrained radius and border |
| `Badge` | tones `neutral`, `accent`, `success`, `warning`, `danger`, `info` |
| `StatusDot` | `tone` plus a visible `label`. Status is never conveyed by colour alone. |
| `Input`, `Textarea`, `Select` | set `invalid` for error styling |
| `Field` | render-prop wrapper: `<Field label hint error>{(c) => <Input {...c} />}</Field>` wires ids and `aria-describedby` |
| `Switch` | `role="switch"`, labelled |
| `Tabs` | roving focus, arrow keys, `count` badges |
| `Dialog` | focus trapped, Escape closes, focus returns to the opener, `placement="bottom"` for sheets |
| `Tooltip` | hint on hover and focus |
| `Skeleton`, `SkeletonList` | loading placeholders (aria-hidden or role=status) |
| `EmptyState`, `ErrorState` | `ErrorState` is `role="alert"` and takes `onRetry` |
| `ToastProvider`, `useToast()` | `toast({ tone: "info" \| "success" \| "error", title, description })` |
| `Kbd`, `Icon` | `Icon` names are listed in `IconName` |
| `cn()` | class name joiner |

**Data.**
- `api` (`services/api.ts`): `get`, `post`, `put`, `patch`, `del`. Unsafe methods send `X-CSRF-Token`. Failures throw `ApiError` (`code`, `status`, `message`, `details`).
- `buildQuery({...})` builds `?a=1&b=2`, dropping empty values.
- `qk` and the query hooks in `services/queries.ts`. Add a hook there for each new endpoint, with its query key in `qk`. Add the event-to-key mapping in `invalidationsForEvent` when a live event should refresh it.
- `streamSSE(path, body, signal)` for any new POST SSE endpoint. `parseChatEvent` is the pattern for typed events.

**Preferences and motion.**
- `useUiStore(selector)` reads theme, accent, reduced motion, orb style and font size.
- `useReducedMotion()` and `useAnimationIntensity()` decide whether decorative motion runs.
- `useAppearanceSync` is mounted once in `AppShell`. Appearance changes are debounced to `PATCH /settings { appearance }`. Do not add a second sync.

**Other.**
- `Markdown` renders assistant text safely. Use it for any model output.
- `AIOrb` with `orbVisual(state)`. The states are `idle`, `listening`, `processing`, `speaking`, `executing`, `awaiting_approval`, `disconnected` and `error`.
- `TaskDetail` and `ToolProposalCard` show a task with its real actions (Approve, Deny, Cancel, Retry). Button enablement comes from `task.actions` through `taskActionStates()`.
- `PairingDialog` shows a one-time code once, and the code is cleared on close.

**Tests.** `src/test/utils.tsx` provides `renderWithProviders(ui, { route, path })`, which adds a fresh QueryClient, a MemoryRouter and the toast host. `src/__tests__/helpers.ts` provides `routeFetch({ "/path": (url) => body })`, a fetch mock that answers by pathname and records the calls, and `taskFixture()`.

## Stub pages

Twelve WEB-B pages did not exist when WEB-A finished. Each is a small stub that says "Being built", so the route table resolves and the build passes. WEB-B overwrites these files:

- `src/pages/schedule/SchedulePage.tsx`
- `src/pages/memory/MemoryPage.tsx`
- `src/pages/notifications/NotificationsPage.tsx`
- `src/pages/explore/ExplorePage.tsx`
- `src/pages/integrations/IntegrationsPage.tsx`
- `src/pages/analytics/AnalyticsPage.tsx`
- `src/pages/activity/ActivityPage.tsx`
- `src/pages/developer/DeveloperPage.tsx`
- `src/pages/settings/SettingsPage.tsx`
- `src/pages/help/HelpPage.tsx`
- `src/pages/automation/AutomationPage.tsx`
- `src/pages/files/FilesPage.tsx`

Each stub has a default export, which the route table lazy-loads.

## Behaviour notes

- **Honest capability.** Voice shows what the browser can do. "Listening" appears only after the recognition engine reports a start, and "speaking" only after the synthesis engine reports an utterance. Wake word is labelled not available. Microphone selection is labelled unsupported when the browser cannot enumerate devices.
- **Keys stay write-only.** The AI Models credential input is never filled from the server. Only `credential_hint` is shown.
- **Pairing codes are shown once.** The code lives in `PairingDialog` state and is cleared when the dialog closes.
- **Device permissions.** "Allow without asking" is offered only for capabilities whose tool is LOW risk. The tool list comes from `GET /tools`.
- **Reduced motion** applies from the setting, or from the OS preference when the setting is "system". A `data-reduced-motion` attribute on `<html>` drives it in CSS as well.
- **Dark by default.** An inline script in `index.html` applies the remembered theme before first paint. Otherwise the theme is dark.

## Phase 2 features

The contracts behind these pages are in `docs/phase2/` (integrations, memory, plugins, late results, desktop). The pages read only the documented fields.

- **Integrations** (`pages/integrations/`). Google Calendar and GitHub can be connected. Connect asks the server for a sign-in address (`POST /integrations/{id}/connect`) and then sends the browser to it. The browser never builds that address, and it leaves the app only for an `https` address. While the browser is leaving, the card says so. The provider returns to `/integrations?provider=…&status=…&reason=…`. The page shows that result once in a banner and then removes the query string with `history.replaceState`. A reason code is shown as readable text, and an unknown code gets the generic message. Provider text is never shown, because none is sent. Each card shows its status as text and only the actions that status allows: Connect (not connected or disconnected), Reconnect (expired), Test connection or Test again, and Disconnect. `requires_configuration` lists the server setting names and has no connect button. Disconnect asks for the typed word `DISCONNECT`. For GitHub the dialog says that the token is deleted locally and is not revoked at GitHub. Granted permissions come from the stored connection. The other five catalogue entries are planned, with no actions.
- **Memory search by meaning** (`pages/memory/`). The "Search by meaning" switch sends `semantic=true`. Keyword search stays the default. Each result list shows `Search mode` and, when keyword search answered a semantic request, the `fallback_reason` with its meaning. "Build search index" calls `POST /memories/reindex`, which embeds up to 200 memories per press, and reports `updated` and `remaining`. A failed build shows the reason and the counts that were saved before the failure.
- **Timezone** (`pages/settings/`, Automation section). "Timezone for reminders and schedules" is a top-level setting. Its options come from `Intl.supportedValuesOf('timeZone')`. Where the browser cannot list zones, a text field is shown instead. Blank means UTC and is saved as `null`, because the API rejects an empty string. Saving sends only `{ "timezone": … }`.
- **Explore and tools** (`pages/explore/`, Automation tool list). Plugin tools such as `current_time` and `calculate` appear through `GET /tools` and `GET /explore`. The web app shows each one under the category the API sends and hardcodes no tool names. The explore service currently files plugin and integration tools under "Automation", so that is what the page shows.
- **Installable app.** `public/manifest.webmanifest` (name "JARVIS AI", short name "JARVIS", start page `/overview`, standalone display, dark theme colours) and PNG icons in `public/icons/` (180, 192 and 512 pixels). `index.html` links the manifest and the apple touch icon and sets the iOS web app options. On an iPad or iPhone, use Share, then Add to Home Screen. Chrome and Edge offer an install action as well.
  - The icons are **placeholder art**: an accent-colour square with a centred J drawn from rectangles. Regenerate them with `python3 scripts/gen-icons.py` (standard library only), or replace the PNG files with brand artwork at the same paths and sizes.
  - **No service worker and no offline support.** Nothing is cached. The app needs a connection to load and to reach the API, and an installed copy still behaves like a web page.
  - The nginx configuration is not changed here. Check that `.webmanifest` is served as `application/manifest+json`. If it is not, add that type mapping to the server config.

## Phase 3 features

The screens and the rules they follow are in `docs/phase3/web.md`. In short:

- **Users** (Settings, owner only). Lists accounts, creates members, deactivates and reactivates them, and resets passwords. Members never see the tab. The server enforces the same rule.
- **Export** (chat header). Downloads a conversation as Markdown or JSON through a Blob URL.
- **Voice** settings are applied: push to talk, interruptions, continuous listening, captions and spoken replies.
- **Browser alerts** (`services/browserNotifications.ts`). Shown only while the tab is hidden, only for the events the settings allow, and generic unless task details are allowed. Permission is requested from a click.
- **Deep links.** `/chat?prompt=...` fills the composer and never sends it. Capped at 2000 characters.
- **Hidden notifications** note on the inbox, linked to Settings, Notifications.
- **Integrations.** Gmail, Google Drive and Notion use the same card as the other providers. The disconnect copy is provider-neutral.
- **Devices** list approved checks, labels only. A check always needs approval.
- **Memory** shows a Summary badge for conversation summaries.

## Phase 4 features

The rules and endpoints are in `docs/phase4/features.md`. The web parts are:

- **Command palette** (`layouts/CommandPalette.tsx`, `layouts/commandPalette.ts`). Ctrl+K or Cmd+K. Pages, actions, and search hits under "Found".
- **Global search** (`useGlobalSearch` in `services/queries.ts`). Two or more characters, debounced in the palette.
- **Quiet hours** (Settings, Notifications) and their rule in `services/quietHours.ts`. Browser pop-ups only.
- **Pinned conversations** and **thumbs ratings** on finished replies in the chat.
- **Task CSV** (Export CSV on the Tasks page) and **Download my data** (Settings, Privacy). Both use `services/exportDownload.ts`.
- **Daily AI call limit** (Settings, AI) and **next runs preview** on the schedule form.
- **First-run checklist** on Overview (`pages/overview/OnboardingCard.tsx`, `onboarding.ts`). Hidden per browser when dismissed.
- **High-contrast theme** (Settings, Appearance).
- **Keyboard shortcuts** listed on Help.

## Tests

`npm test` runs vitest in jsdom. Phase 3 adds `web-phase3-rules` and `web-phase3-ui`. Phase 4 adds `web-phase4-*`
(export, quiet, palette, feedback, pin, onboarding, schedule, theme, account). The suites are `api`, `sse`, `markdown`, `aiorb`, `voice-store`, `tasks-filter`, `devices-pairing`, `models-credential`, `overview-logic`, `chat-state`, `ui-store` and `routing`, and the phase 2 suites `web-b-integrations`, `web-b-memory-semantic`, `web-b-settings-timezone`, `web-b-explore` and `web-b-pwa`. End-to-end tests (Playwright) are written by the integrator, not in this app.
