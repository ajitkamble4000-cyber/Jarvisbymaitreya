/**
 * The quick-ask popup. A small frameless window shows the web app's /quick page. It opens with the hotkey, closes when
 * it loses focus or when Escape is pressed, and loads the page again each time it opens, so no earlier text stays.
 * It can reach only the web app's own origin, and it opens no other window.
 */
import { BrowserWindow, app, ipcMain, screen, type IpcMainEvent } from "electron";
import { IPC } from "../shared/ipc-contract.js";
import { centredPosition, isQuickOrigin, QUICK_WINDOW_SIZE } from "../shared/quick.js";
import { guardWebContentsFor } from "./window.js";

export interface QuickWindowOptions {
  /** The web app's origin, for example http://127.0.0.1:5173. Only pages on this origin may be shown. */
  origin: string;
  /** The full address of the quick-ask page. */
  url: string;
  preloadPath: string;
  /** Called when the page could not be loaded, so the tray can say why. */
  onLoadFailure: (message: string) => void;
}

export interface QuickWindow {
  toggle: () => void;
  hide: () => void;
  dispose: () => void;
}

export function createQuickWindow(options: QuickWindowOptions): QuickWindow {
  let window: BrowserWindow | null = null;
  const isAllowed = (url: string): boolean => isQuickOrigin(url, options.origin);

  const create = (): BrowserWindow => {
    const created = new BrowserWindow({
      ...QUICK_WINDOW_SIZE,
      show: false,
      frame: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      autoHideMenuBar: true,
      title: "JARVIS",
      webPreferences: {
        preload: options.preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
        nodeIntegrationInSubFrames: false,
        nodeIntegrationInWorker: false,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        experimentalFeatures: false,
        webviewTag: false,
        devTools: !app.isPackaged,
        spellcheck: false,
      },
    });
    created.setAlwaysOnTop(true, "floating");
    guardWebContentsFor(created.webContents, isAllowed);
    // A popup that lost focus is finished with: it closes, as a launcher does.
    created.on("blur", () => hide());
    created.on("closed", () => {
      window = null;
    });
    return created;
  };

  const show = (): void => {
    if (window === null || window.isDestroyed()) {
      window = create();
    }
    const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    const { x, y } = centredPosition(display.workArea, QUICK_WINDOW_SIZE);
    window.setPosition(x, y);
    const current = window;
    current.loadURL(options.url).catch(() => {
      options.onLoadFailure(`The quick window could not reach ${options.origin}. Start the web app, then try again.`);
      if (!current.isDestroyed()) current.hide();
    });
    current.show();
    current.focus();
  };

  function hide(): void {
    if (window !== null && !window.isDestroyed()) {
      window.hide();
    }
  }

  // Escape in the popup asks the main process to hide it. Only the popup's own page may send this.
  const onHide = (event: IpcMainEvent): void => {
    if (window !== null && event.sender === window.webContents && event.senderFrame && isAllowed(event.senderFrame.url)) {
      hide();
    }
  };
  ipcMain.on(IPC.quickHide, onHide);

  return {
    toggle: () => {
      if (window !== null && !window.isDestroyed() && window.isVisible()) {
        hide();
      } else {
        show();
      }
    },
    hide,
    dispose: () => {
      ipcMain.removeListener(IPC.quickHide, onHide);
      if (window !== null && !window.isDestroyed()) window.destroy();
      window = null;
    },
  };
}
