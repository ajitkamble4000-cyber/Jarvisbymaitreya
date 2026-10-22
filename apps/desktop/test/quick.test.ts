import { describe, expect, it } from "vitest";
import {
  DEFAULT_HOTKEY,
  DEFAULT_WEB_URL,
  centredPosition,
  chooseHotkey,
  isQuickMicrophoneRequest,
  isQuickOrigin,
  quickAskUrl,
  quickWebOrigin,
} from "../src/shared/quick.js";

describe("quick ask address", () => {
  it("defaults to the local web app", () => {
    expect(quickWebOrigin(undefined)).toBe(DEFAULT_WEB_URL);
    expect(quickWebOrigin("   ")).toBe(DEFAULT_WEB_URL);
  });

  it("keeps only the origin of the configured web address", () => {
    expect(quickWebOrigin("https://jarvis.example.com")).toBe("https://jarvis.example.com");
    expect(quickAskUrl("https://jarvis.example.com")).toBe("https://jarvis.example.com/quick");
  });

  it("refuses an address with a path or on a remote host without https", () => {
    expect(() => quickWebOrigin("https://jarvis.example.com/app")).toThrow();
    expect(() => quickWebOrigin("http://jarvis.example.com")).toThrow();
  });

  it("recognises only the web app's own origin", () => {
    const origin = "http://127.0.0.1:5173";
    expect(isQuickOrigin("http://127.0.0.1:5173/quick?ask=hi", origin)).toBe(true);
    expect(isQuickOrigin("http://127.0.0.1:5173/login", origin)).toBe(true);
    expect(isQuickOrigin("http://127.0.0.1:8000/api/v1/health/live", origin)).toBe(false);
    expect(isQuickOrigin("http://127.0.0.1:5173.evil.test/", origin)).toBe(false);
    expect(isQuickOrigin("file:///C:/app/index.html", origin)).toBe(false);
    expect(isQuickOrigin("not a url", origin)).toBe(false);
  });
});

describe("quick ask hotkey", () => {
  it("uses Control+Space when nothing is configured", () => {
    expect(chooseHotkey(undefined)).toEqual({ accelerator: DEFAULT_HOTKEY, warning: null });
    expect(DEFAULT_HOTKEY).toBe("Control+Space");
  });

  it.each(["Control+Shift+J", "Alt+Space", "CommandOrControl+Alt+K", "Super+Shift+Space"])("accepts %s", (value) => {
    expect(chooseHotkey(value)).toEqual({ accelerator: value, warning: null });
  });

  it.each(["Space", "Control", "Control+", "Control+Space+Shift", "Control+Hello World", "Control+;", "x".repeat(60)])(
    "refuses %s and says why",
    (value) => {
      const choice = chooseHotkey(value);
      expect(choice.accelerator).toBe(DEFAULT_HOTKEY);
      expect(choice.warning).toContain("is not a usable shortcut");
    },
  );
});

describe("quick ask position", () => {
  it("centres the window in the work area", () => {
    expect(centredPosition({ x: 0, y: 0, width: 1920, height: 1040 }, { width: 640, height: 430 })).toEqual({ x: 640, y: 305 });
  });

  it("keeps the window on a second screen by using that screen's offset", () => {
    expect(centredPosition({ x: 1920, y: 0, width: 1280, height: 1000 }, { width: 640, height: 400 })).toEqual({ x: 2240, y: 300 });
  });

  it("never places a window larger than the work area off screen", () => {
    expect(centredPosition({ x: 0, y: 0, width: 500, height: 300 }, { width: 640, height: 430 })).toEqual({ x: 0, y: 0 });
  });
});

describe("quick ask microphone permission", () => {
  const origin = "http://127.0.0.1:5173";

  it("allows the microphone for the popup's own origin only", () => {
    expect(isQuickMicrophoneRequest("media", origin, origin, ["audio"])).toBe(true);
    expect(isQuickMicrophoneRequest("media", "http://evil.test", origin, ["audio"])).toBe(false);
    expect(isQuickMicrophoneRequest("media", origin, null, ["audio"])).toBe(false);
  });

  it("refuses the camera and every other permission", () => {
    expect(isQuickMicrophoneRequest("media", origin, origin, ["audio", "video"])).toBe(false);
    expect(isQuickMicrophoneRequest("media", origin, origin, ["video"])).toBe(false);
    expect(isQuickMicrophoneRequest("geolocation", origin, origin)).toBe(false);
    expect(isQuickMicrophoneRequest("notifications", origin, origin)).toBe(false);
  });
});
