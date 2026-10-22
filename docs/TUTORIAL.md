# JARVIS AI: what it is and how to use it

This tutorial starts from a fresh download of the repository and ends with your first approved action on your PC.
Each step says what you should see. If a step does not match, the troubleshooting section at the end lists the usual causes.

## What JARVIS is

JARVIS is a personal AI assistant with three parts:

| Part | What it does | Where it runs |
|---|---|---|
| **Backend** (`services/api`) | Holds your account, your AI provider key (encrypted), conversations, tasks, schedules and memory. It talks to the AI model and to your paired PC. | Your machine, in Docker or as a Python process |
| **Web app** (`apps/web`) | The command centre: chat, voice, tasks, approvals, devices, settings and the activity log. | Your browser, at `http://127.0.0.1:5173` |
| **Desktop agent** (`apps/desktop`) | A small Windows program (Electron) that runs approved actions on the PC you pair with it. | The PC you want JARVIS to control |

JARVIS never acts on its own. When the assistant wants to do something on your PC, it creates a **task** and waits. You
see the task, press **Approve** or **Deny**, and only then does the desktop agent run it. The agent also checks the
request against its own allowlist (`agent.json`). If an app, folder or website is not on that list, it does not run,
whatever the assistant asked for.

## Before you start

You need:

- **Git** to download the repository.
- **Docker Desktop** (Windows or macOS) or Docker Engine (Linux). This is the simplest way to run the backend and the web app.
- **A Google Gemini API key.** Create one in Google AI Studio. JARVIS stores it encrypted; you paste it into the web app once.
- **Windows 10 or 11** for the desktop agent. The backend and web app run on any system that runs Docker.
- **Node.js 22** on the PC where you build or run the desktop agent (Windows).

The repository does not contain your keys. Your local settings live in a file called `.env`, which is created from a template
and never committed.

## Part 1: install from GitHub

### 1.1 Download the repository

```sh
git clone https://github.com/ajitkamble4000-cyber/jarvisbymaitreya.git
cd jarvisbymaitreya
```

### 1.2 Create your settings file

```sh
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
```

For a first run on your own PC you can keep the defaults. The Docker setup overrides the database address itself.
Two values matter later:

- `ENCRYPTION_KEY` is empty by default. In development JARVIS creates a key on first start and keeps it in its data
  folder. Do not delete that folder, or stored AI keys become unreadable.
- `ALLOWED_ORIGINS` lists the web addresses allowed to talk to the API. The default includes `http://127.0.0.1:5173`.

### 1.3 Start the stack

```sh
docker compose up --build
```

The first build takes a few minutes. Wait until the log shows the API as healthy. Then open another terminal and check:

```sh
curl http://127.0.0.1:8000/api/v1/health/live
```

You should see a JSON answer with `"status":"ok"`. If you prefer not to use Docker, see **Appendix A** for the local
development setup with Python and Node.

To stop the stack later, press `Ctrl+C` in the first terminal, or run `docker compose down`. Your data stays in Docker
volumes until you run `docker compose down -v`.

## Part 2: first-time setup in the web app

### 2.1 Create the owner account

Open **http://127.0.0.1:5173**. The first screen asks you to create the owner account: an email address and a password of
at least 12 characters. This account is the only one that can manage other people and devices. Remember the password;
there is no email recovery yet.

### 2.2 Add your AI key

1. Go to **AI Models** in the left menu.
2. Paste your Gemini API key and press **Save key**. The key is encrypted at rest and is never shown again.
3. Press **Test connection**. You should see a success message.
4. Choose a model from the live list. Replies in **Chat** use this model.

If the test fails, check that the key is copied in full and that your Google project allows the Gemini API.

### 2.3 Check the Overview

**Overview** shows a first-run checklist: an AI key, a model, a paired desktop agent, your time zone for reminders and a
first message. Steps turn into ticks as you complete them. You can hide the checklist once you are done.

## Part 3: pair your Windows PC

The desktop agent is what lets JARVIS act on your computer. Pairing connects one PC to your account.

### 3.1 Get a pairing code

1. In the web app, open **Devices** (the *Connected Devices* page).
2. Press **Create pairing code**. You get a code such as `ABCD-EFGH`. It expires after 10 minutes.

### 3.2 Run the agent

On the Windows PC, open PowerShell in the folder with the repository:

```powershell
cd apps\desktop
npm install
npm start
```

A window called **JARVIS Agent** opens. The first screen asks for two things:

- **Server address:** `http://127.0.0.1:8000` if the backend runs on the same PC. Use `https://` for a server on the internet.
- **Pairing code:** the code from step 3.1.

Press **Pair**. The window should change to *Connected*. The web app's **Devices** page then shows your PC as *online*.

The agent also stays in the system tray (the icons near the clock). Closing its window does not stop it. Use the tray
menu's **Quit** to stop it.

### 3.3 Set what JARVIS may do

The agent keeps its allowlist in `%APPDATA%\JARVIS\agent.json`. Open the folder from the agent window (**Open config folder**).

If the file does not exist yet, the agent uses these built-in rules:

- **Apps:** Google Chrome and Visual Studio Code, found in their usual install folders.
- **Capabilities** switched on: open a web address, launch an approved app, open an approved folder, open an approved project,
  read system information, read an approved file, and check whether an app is installed.
- **Web addresses, folders, files and projects:** none until you add them.

To change the rules, create `agent.json` in that folder. This file adds one folder and keeps the built-in capabilities:

```json
{
  "version": 1,
  "approvedApps": [],
  "approvedUrls": [],
  "approvedFolders": [
    { "id": "downloads", "label": "Downloads", "path": "C:\\Users\\you\\Downloads" }
  ],
  "approvedProjects": [],
  "approvedFiles": [],
  "capabilities": {
    "open_url": true,
    "launch_app": true,
    "open_folder": true,
    "open_project": true,
    "system_info": true,
    "read_file": true,
    "app_availability": true
  },
  "approvedChecks": [],
  "pairing": null
}
```

The example has `"pairing": null`, which is right before the first pairing. Once the agent is paired, keep the `"pairing"` object the agent wrote there. Setting it to `null` unpairs the agent, and you would need a new code.
Edit only the lists you need. Each `id` is lower-case letters, digits, `-` or `_`, up to 64 characters. The assistant can refer
only to these ids. After editing, stop and start the agent from the tray menu (**Stop**, then **Start**).

## Part 4: your first approved action

1. Open **Chat** and start a new conversation.
2. Type: **open Google Chrome on my laptop**. (Use the name of your PC if you paired more than one.)
3. JARVIS replies with a proposed task and shows an **Approve** and a **Deny** button. Nothing has run yet.
4. Press **Approve**.
5. The task moves through *Running* to *Completed*. Chrome opens on your PC.

You can follow the same task in **Tasks**: the timeline shows each step, the device that ran it and the result. **Activity**
shows the whole audit trail with the outcome of every action.

If you press **Deny**, the task ends and nothing runs.

## Part 5: ask from anywhere (the popup and "Hey JARVIS")

Once the desktop agent is running, JARVIS works like a launcher. You do not need to open the web app.

### 5.1 The popup: Ctrl+Space

1. Press **Ctrl+Space** anywhere on the PC. A small window appears in the middle of the screen.
2. Type what you want, for example **open Google Chrome on my laptop**, and press **Enter**.
3. The reply appears in the window. If JARVIS wants to act, an **Approve** and **Deny** card appears in the same window.
4. Press **Approve**. The result appears there too, and the full conversation is in **Chat** under the title you saw.
5. Press **Esc**, or click anywhere else, to close the window. The next Ctrl+Space starts with an empty box.

The popup is the web app's **/quick** page shown in a small frameless window. Two things follow from that:

- The web app must be running (step 1.3). If it is not, the window says it cannot reach the address above.
- The popup keeps its own sign-in, separate from your browser. If it shows the sign-in page, sign in once inside it.

Settings you can change with environment variables, set before the agent starts:

| Variable | Default | Use it for |
|---|---|---|
| `JARVIS_HOTKEY` | `Control+Space` | Another shortcut, such as `Alt+Space` or `Control+Shift+J`. It needs at least one modifier and one key. |
| `JARVIS_WEB_URL` | `http://127.0.0.1:5173` | The web app's address, if it runs somewhere else. Use `https` for a server on the internet. |

If another program already uses the shortcut, the tray menu says so and names the variable to change. The popup then
still opens from the tray: **Quick ask** in the tray menu.

The tray menu also has **Start with Windows** (installed app only), so the agent is ready after you sign in to Windows.

### 5.2 "Hey JARVIS": speaking to JARVIS

1. In the web app, open **Settings → Voice** and switch on **Hey JARVIS**.
2. Keep a JARVIS page open in **Chrome or Edge**. A small label in the bottom-left corner says **Listening for “Hey JARVIS”**.
   The label is there so you always know when the microphone is on.
3. Say the phrase with the request in one sentence: **"Hey JARVIS, open Google Chrome on my laptop."**
   The quick window opens with that request already sent. The approval card appears as usual.
4. Or say **"Hey JARVIS"** on its own. The label changes to *Listening for your request*. Say the request within 8 seconds.

Things to know:

- The wake phrase is **hey**, **hi**, **ok** or **okay**, followed by **Jarvis**, anywhere in the sentence. A mention of the name
  on its own does not wake JARVIS.
- Chrome and Edge send the audio to their speech service on the internet. JARVIS does not record or store the audio.
- The listener stops if the browser refuses the microphone, and the label says why. Allow the microphone for the site in the
  browser's site settings, then switch **Hey JARVIS** off and on again.
- The Voice page has its own microphone button. While you use it, the listener pauses, so the two do not compete.
- The desktop popup also has a microphone button. Its speech recognition depends on Electron's speech service, which was not
  verified in this project. Typing in the popup always works.

## Part 6: everyday use

| What you want | Where |
|---|---|
| Ask questions, plan, or request an action | **Chat** |
| Speak to JARVIS and hear replies | **Voice** (needs a browser with speech support, such as Chrome or Edge) |
| Read a reply aloud | the speaker button under any finished reply |
| Find text in a long conversation | the find box above the messages |
| Jump to a page, conversation or action | `Ctrl+K` (or `Cmd+K` on a Mac) opens the command palette |
| Search everything | type two or more letters in the command palette |
| Run something later or on a repeat | **Schedule** (check the "Next runs" preview before saving) |
| See what the assistant remembers | **Memory** (you can edit or delete any memory) |
| Review what happened | **Activity** (filter by action, outcome and dates; export as CSV) |
| Quiet pop-ups at night | **Settings → Notifications → Quiet hours** |
| Keep the daily AI cost down | **Settings → AI → Daily AI call limit** (the AI page shows today's count) |
| Take your data with you | **Settings → Privacy → Download my data** |

**Settings** groups everything else: voice, appearance (including a high-contrast theme), automation, security (sessions and
password), privacy, and, for the owner, **Users** to add family members or colleagues.

## Part 7: keeping it secure

- Use a strong owner password. Anyone with it can approve actions on your PC.
- Keep the allowlist short. Add only folders and apps you want JARVIS to open.
- Read each proposed task before you approve it. The task shows the tool, the arguments and the device.
- Do not put your `.env` file, your `ENCRYPTION_KEY` or your AI key into a chat, a screenshot or a ticket.
- Sign out from **Settings → Security** on a shared computer. Unsent chat text is removed from that browser when you sign out.

## Troubleshooting

| Problem | What to check |
|---|---|
| `docker compose up` stops with a port error | Another program uses port 5173, 8000 or 5432. Change the host port in `docker-compose.yml`, or set `POSTGRES_HOST_PORT` in `.env`. |
| The web page says it cannot reach the server | Wait until `api` shows healthy in Docker. Then reload the page. |
| "Test connection" fails for Gemini | Check the key, and that the Gemini API is enabled for the Google project that created it. |
| The pairing code is refused | Codes expire after 10 minutes and allow five attempts. Create a new code. |
| The agent says *Offline, reconnecting* | The backend is not reachable from the PC. For a server on your PC, use `http://127.0.0.1:8000`. |
| An action says *not on the allowlist* | Add the app, folder or address to `agent.json` and restart the agent. |
| A task waits forever | Open **Tasks** and approve or deny it. Tasks that are never answered expire after 10 minutes. |
| Voice does nothing | Use Chrome or Edge, allow the microphone for the site, and check **Voice** shows a microphone. |
| The API will not start after an update | Stop the stack, then run `docker compose up --build` again. Migrations run on start. |

## Limits you should know about

- The desktop agent is verified on Linux with a simulated platform. Real Windows actions have not been run in this project's
  test environment; test them with harmless apps first.
- The daily AI call limit counts provider calls, not tokens.
- Browser notifications appear only while the web app's tab is open in the background.
- Voice depends on your browser's speech engine. Some browsers send speech to an online service.
- There is no email recovery, invitation email or password reset yet. The owner sets each member's first password.

## Appendix A: run without Docker (developers)

You need Python 3.13, Node.js 22, and PostgreSQL 16 with the `pgvector` extension. The README has the exact database
commands in its section **Run it locally**. In short:

```sh
cd services/api && python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]" && cd ../..
scripts/dev-backend.sh        # API on http://127.0.0.1:8000
scripts/dev-web.sh            # web app on http://127.0.0.1:5173, in a second terminal
```

## Where to read more

- [README.md](../README.md): what has been verified, and how
- [docs/api.md](api.md): every endpoint
- [docs/desktop-protocol.md](desktop-protocol.md): how the agent and the server talk
- [docs/phase4/features.md](phase4/features.md): the newest features, with their tests
- [docs/operations.md](operations.md): backups, keys and monitoring
