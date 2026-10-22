/**
 * Window creation and navigation guards. The renderer is a local file. It may not navigate elsewhere, open windows,
 * attach webviews or use any browser permission.
 */
import * as path from "node:path";
import { pathToFileURL } from "node:url";
import { BrowserWindow, type WebContents } from "electron";

export function rendererIndexFile(): string {
  return path.join(__dirname, "..", "renderer", "index.html");
}

export function rendererUrl(): string {
  return pathToFileURL(rendererIndexFile()).href;
}

export function preloadFile(): string {
  return path.join(__dirname, "..", "preload", "index.js");
}

/** Only the renderer's own file URL is allowed. Anything else, including other file URLs, is refused. */
export function isAllowedUrl(url: string, allowedUrl: string): boolean {
  try {
    return new URL(url).href === new URL(allowedUrl).href;
  } catch {
    return false;
  }
}

/** Applies the navigation and window rules to one web contents. Called for every window, including future ones. */
export function guardWebContents(contents: WebContents, allowedUrl: string): void {
  guardWebContentsFor(contents, (url) => isAllowedUrl(url, allowedUrl));
}

/**
 * The same rules for a window that may move between pages of one site (the quick-ask popup). `isAllowed` decides
 * which addresses the window may show; everything else is refused, and no new window can be opened.
 */
export function guardWebContentsFor(contents: WebContents, isAllowed: (url: string) => boolean): void {
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event, url) => {
    if (!isAllowed(url)) {
      event.preventDefault();
    }
  });
  contents.on("will-redirect", (event, url) => {
    if (!isAllowed(url)) {
      event.preventDefault();
    }
  });
  contents.on("will-frame-navigate", (details) => {
    if (!isAllowed(details.url)) {
      details.preventDefault();
    }
  });
  contents.on("will-attach-webview", (event) => {
    event.preventDefault();
  });
}

export interface WindowOptions {
  preloadPath: string;
  allowedUrl: string;
  devTools: boolean;
}

export function createMainWindow(options: WindowOptions): BrowserWindow {
  const window = new BrowserWindow({
    width: 560,
    height: 680,
    minWidth: 480,
    minHeight: 560,
    show: false,
    title: "JARVIS Agent",
    autoHideMenuBar: true,
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
      devTools: options.devTools,
      spellcheck: false,
    },
  });
  guardWebContents(window.webContents, options.allowedUrl);
  window.once("ready-to-show", () => window.show());
  void window.loadFile(rendererIndexFile());
  return window;
}
