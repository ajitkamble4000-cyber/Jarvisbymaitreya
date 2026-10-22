/**
 * The quick-ask popup: its address, its hotkey, and where it appears. Pure, so each rule is tested without Electron.
 * The popup shows the web app's /quick page. It runs in the same origin as the web app, so it can reach the API the
 * same way the browser does.
 */
import { normaliseServerUrl } from "../agent/server-url.js";

export const DEFAULT_WEB_URL = "http://127.0.0.1:5173";
export const DEFAULT_HOTKEY = "Control+Space";
export const QUICK_PATH = "/quick";
export const QUICK_WINDOW_SIZE = { width: 640, height: 430 } as const;

const HOTKEY_PATTERN = /^[A-Za-z0-9]+(\+[A-Za-z0-9]+)*$/;
const HOTKEY_MAX_LENGTH = 40;
const MODIFIERS = new Set([
  "control",
  "ctrl",
  "commandorcontrol",
  "cmdorctrl",
  "shift",
  "alt",
  "option",
  "altgr",
  "super",
  "command",
  "cmd",
  "meta",
]);

/** The origin of the web app that serves /quick, for example http://127.0.0.1:5173. Only https or loopback is accepted. */
export function quickWebOrigin(configured: string | undefined): string {
  const value = configured?.trim() ? configured : DEFAULT_WEB_URL;
  return normaliseServerUrl(value).origin;
}

export function quickAskUrl(origin: string): string {
  return `${origin}${QUICK_PATH}`;
}

/** True when the URL belongs to the web app's origin, whatever its path or query. */
export function isQuickOrigin(url: string, origin: string): boolean {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

export interface HotkeyChoice {
  accelerator: string;
  /** Set when the configured value was refused and the default is used instead. */
  warning: string | null;
}

/**
 * An Electron accelerator such as Control+Space. It needs at least one modifier and exactly one key after them. An
 * unusable value falls back to the default, and the warning says why.
 */
export function chooseHotkey(configured: string | undefined): HotkeyChoice {
  const value = configured?.trim() ?? "";
  if (value === "") return { accelerator: DEFAULT_HOTKEY, warning: null };
  const parts = value.split("+");
  const key = parts[parts.length - 1] ?? "";
  const modifiers = parts.slice(0, -1);
  const valid =
    value.length <= HOTKEY_MAX_LENGTH &&
    HOTKEY_PATTERN.test(value) &&
    modifiers.length > 0 &&
    modifiers.every((modifier) => MODIFIERS.has(modifier.toLowerCase())) &&
    key.length > 0 &&
    !MODIFIERS.has(key.toLowerCase());
  if (valid) return { accelerator: value, warning: null };
  return {
    accelerator: DEFAULT_HOTKEY,
    warning: `JARVIS_HOTKEY "${value.slice(0, HOTKEY_MAX_LENGTH)}" is not a usable shortcut; ${DEFAULT_HOTKEY} is used.`,
  };
}

/**
 * The one permission the app grants: the microphone, for the quick-ask popup's own origin. Every other permission is
 * refused everywhere, as before. Camera requests are refused even from the popup.
 */
export function isQuickMicrophoneRequest(
  permission: string,
  requestingOrigin: string,
  quickOrigin: string | null,
  mediaTypes?: readonly string[],
): boolean {
  if (permission !== "media" || quickOrigin === null || requestingOrigin !== quickOrigin) return false;
  return mediaTypes === undefined || mediaTypes.every((type) => type === "audio");
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The top-left corner that centres a window of the given size in a work area, with the window kept on screen. */
export function centredPosition(workArea: Rect, size: { width: number; height: number }): { x: number; y: number } {
  const width = Math.min(size.width, workArea.width);
  const height = Math.min(size.height, workArea.height);
  return {
    x: workArea.x + Math.round((workArea.width - width) / 2),
    y: workArea.y + Math.round((workArea.height - height) / 2),
  };
}
