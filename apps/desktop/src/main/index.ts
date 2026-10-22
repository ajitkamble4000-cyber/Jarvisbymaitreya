/**
 * Electron main process: single instance, tray menu with status, the agent window, and the agent lifecycle.
 * The agent runs here, not in the renderer. The renderer only sees the typed API from the preload script.
 */
import * as path from "node:path";
import {
  Menu,
  Tray,
  app,
  globalShortcut,
  nativeImage,
  safeStorage,
  session,
  shell,
  type BrowserWindow as BrowserWindowType,
  type MenuItemConstructorOptions,
  type WebContents,
} from "electron";
import { Agent } from "../agent/agent.js";
import { APP_FOLDER_NAME } from "../agent/config.js";
import { createLogger } from "../agent/logger.js";
import { SafeStorageSecretStore } from "../agent/secret-store.js";
import type { Platform } from "../platform/platform.js";
import { SimulatedPlatform } from "../platform/simulated.js";
import { WindowsPlatform } from "../platform/windows.js";
import type { AgentStatus } from "../shared/ipc-contract.js";
import { DEFAULT_HOTKEY, chooseHotkey, isQuickMicrophoneRequest, quickAskUrl, quickWebOrigin } from "../shared/quick.js";
import { broadcastStatus, registerIpcHandlers } from "./ipc.js";
import { createQuickWindow, type QuickWindow } from "./quick-window.js";
import { createMainWindow, guardWebContents, preloadFile, rendererUrl } from "./window.js";

const SECRET_FILE_NAME = "secrets.bin.json";

let agent: Agent | null = null;
let mainWindow: BrowserWindowType | null = null;
let tray: Tray | null = null;
let quitting = false;
let removeIpc: (() => void) | null = null;
let quick: QuickWindow | null = null;
let quickOrigin: string | null = null;
/** Why the quick ask is unavailable, or a warning about it. Shown in the tray menu. Null when all is well. */
let quickNote: string | null = null;
let hotkeyAccelerator = DEFAULT_HOTKEY;

function choosePlatform(): Platform {
  // Real actions only on Windows. Other systems (development machines) get the simulated platform, which says so.
  if (process.platform === "win32" && process.env.JARVIS_PLATFORM !== "simulated") {
    return new WindowsPlatform({ openExternal: (url) => shell.openExternal(url) });
  }
  return new SimulatedPlatform();
}

function statusLabel(status: AgentStatus): string {
  switch (status.state) {
    case "connecting":
      return "Connecting";
    case "connected":
      return "Connected";
    case "offline":
      return "Offline, reconnecting";
    case "paired":
      return "Paired, not running";
    case "unpaired":
      return "Not paired";
    case "re-pair-required":
      return "Pair again required";
    case "error":
      return status.message === null ? "Error" : `Error: ${status.message}`;
  }
}

function showWindow(): void {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    mainWindow = createAgentWindow();
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

function createAgentWindow(): BrowserWindowType {
  const window = createMainWindow({
    preloadPath: preloadFile(),
    allowedUrl: rendererUrl(),
    devTools: !app.isPackaged,
  });
  // Closing the window keeps the agent running in the tray. Quit is the tray's Quit item.
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      window.hide();
    }
  });
  return window;
}

function rebuildTray(status: AgentStatus): void {
  if (tray === null || agent === null) {
    return;
  }
  const running = agent.isRunning();
  const template: MenuItemConstructorOptions[] = [
    { label: `JARVIS Agent: ${statusLabel(status)}`, enabled: false },
    ...(status.banner === null ? [] : [{ label: status.banner, enabled: false }]),
    { type: "separator" },
    { label: "Start", enabled: !running, click: () => void agent?.start() },
    { label: "Stop", enabled: running, click: () => void agent?.stop() },
    { label: "Open window", click: () => showWindow() },
    { label: `Quick ask (${hotkeyAccelerator})`, enabled: quick !== null, click: () => quick?.toggle() },
    ...(quickNote === null ? [] : [{ label: quickNote, enabled: false }]),
    { type: "separator" },
    {
      label: "Start with Windows",
      type: "checkbox",
      checked: app.getLoginItemSettings().openAtLogin,
      enabled: app.isPackaged,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }),
    },
    { type: "separator" },
    { label: "Quit", click: () => app.quit() },
  ];
  tray.setContextMenu(Menu.buildFromTemplate(template));
  tray.setToolTip(`JARVIS Agent - ${statusLabel(status)}`);
}

/** The origin of the requesting page, when it has one. Requests from file pages have no web origin. */
function quickRequester(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

function quickPreloadFile(): string {
  return path.join(__dirname, "..", "preload", "quick.js");
}

/**
 * Registers the hotkey and creates the popup. The web address comes from JARVIS_WEB_URL (the web app's address,
 * http://127.0.0.1:5173 by default). A bad address or a taken hotkey switches the feature off and says why in the tray.
 */
function setupQuickAsk(): void {
  let origin: string;
  try {
    origin = quickWebOrigin(process.env.JARVIS_WEB_URL);
  } catch {
    quickNote = "Quick ask is off: JARVIS_WEB_URL is not a valid web address.";
    return;
  }
  quickOrigin = origin;
  quick = createQuickWindow({
    origin,
    url: quickAskUrl(origin),
    preloadPath: quickPreloadFile(),
    onLoadFailure: (message) => {
      quickNote = message;
      if (agent !== null) rebuildTray(agent.getStatus());
    },
  });
  const choice = chooseHotkey(process.env.JARVIS_HOTKEY);
  hotkeyAccelerator = choice.accelerator;
  const registered = globalShortcut.register(choice.accelerator, () => quick?.toggle());
  if (registered) {
    quickNote = choice.warning;
  } else {
    quickNote = `${choice.accelerator} is used by another program. Set JARVIS_HOTKEY to another shortcut.`;
  }
}

function createTray(): void {
  const iconPath = path.join(__dirname, "..", "..", "assets", "tray.png");
  tray = new Tray(nativeImage.createFromPath(iconPath));
  tray.on("click", () => showWindow());
}

async function bootstrap(): Promise<void> {
  // Every permission request is refused. Every web contents, including ones created later, gets the navigation rules.
  // The microphone is granted to the quick-ask popup's own origin and to nothing else.
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback, details) => {
    const mediaTypes = "mediaTypes" in details ? details.mediaTypes : undefined;
    callback(isQuickMicrophoneRequest(permission, quickRequester(details.requestingUrl), quickOrigin, mediaTypes));
  });
  session.defaultSession.setPermissionCheckHandler((_contents, permission, requestingOrigin) =>
    isQuickMicrophoneRequest(permission, requestingOrigin, quickOrigin),
  );
  app.on("web-contents-created", (_event, contents: WebContents) => {
    guardWebContents(contents, rendererUrl());
  });

  const dataDir = path.join(app.getPath("appData"), APP_FOLDER_NAME);
  const platform = choosePlatform();
  agent = new Agent({
    dataDir,
    platform,
    secretStore: new SafeStorageSecretStore(safeStorage, path.join(dataDir, SECRET_FILE_NAME)),
    logger: createLogger((level, line) => {
      if (level !== "debug") {
        process.stdout.write(`${line}\n`);
      }
    }),
  });

  agent.onStatusChange((status) => {
    broadcastStatus(status);
    rebuildTray(status);
  });

  await agent.init();
  removeIpc = registerIpcHandlers({
    agent,
    allowedUrl: rendererUrl(),
    openConfigFolder: async () => {
      await agent?.ensureDataDir();
      const failure = await shell.openPath(dataDir);
      if (failure !== "") {
        throw new Error("The configuration folder could not be opened.");
      }
    },
  });

  setupQuickAsk();
  createTray();
  rebuildTray(agent.getStatus());
  showWindow();
  await agent.start();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => showWindow());

  app.whenReady().then(bootstrap).catch(() => {
    process.stderr.write("The JARVIS agent could not start.\n");
    app.exit(1);
  });

  // The app keeps running in the tray when its window is closed.
  app.on("window-all-closed", () => undefined);

  app.on("before-quit", (event) => {
    if (quitting || agent === null) {
      return;
    }
    event.preventDefault();
    quitting = true;
    removeIpc?.();
    globalShortcut.unregisterAll();
    quick?.dispose();
    const current = agent;
    void current.stop().finally(() => app.quit());
  });
}
