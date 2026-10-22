import { describe, expect, it, vi } from "vitest";
import { PairInputSchema } from "../src/shared/ipc-schemas.js";
import { parseHeadlessArgs } from "../src/cli/headless.js";

// window.ts imports Electron at load time. The guard logic is tested with Electron replaced by a stub.
vi.mock("electron", () => ({
  BrowserWindow: class {},
}));

describe("PairInputSchema (IPC payload validation)", () => {
  it("accepts a normal pairing request", () => {
    const parsed = PairInputSchema.safeParse({ serverUrl: "https://jarvis.example.com", code: "KQ7M-2XBV", deviceName: "Laptop" });
    expect(parsed.success).toBe(true);
  });

  it("rejects unknown keys", () => {
    const parsed = PairInputSchema.safeParse({ serverUrl: "https://jarvis.example.com", code: "KQ7M-2XBV", command: "run" });
    expect(parsed.success).toBe(false);
  });

  it("rejects a code with characters outside the pairing alphabet", () => {
    expect(PairInputSchema.safeParse({ serverUrl: "https://jarvis.example.com", code: "KQ7M;rm -rf" }).success).toBe(false);
    expect(PairInputSchema.safeParse({ serverUrl: "https://jarvis.example.com", code: "<script>" }).success).toBe(false);
  });

  it("rejects an over-long or empty server address", () => {
    expect(PairInputSchema.safeParse({ serverUrl: "", code: "KQ7M-2XBV" }).success).toBe(false);
    expect(PairInputSchema.safeParse({ serverUrl: `https://${"a".repeat(3000)}.com`, code: "KQ7M-2XBV" }).success).toBe(false);
  });

  it("rejects a device name with control characters", () => {
    expect(
      PairInputSchema.safeParse({ serverUrl: "https://jarvis.example.com", code: "KQ7M-2XBV", deviceName: "bad\u0007name" }).success,
    ).toBe(false);
  });
});

describe("window navigation guards", () => {
  it("allows only the renderer's own file URL", async () => {
    const { isAllowedUrl } = await import("../src/main/window.js");
    const allowed = "file:///app/dist/renderer/index.html";
    expect(isAllowedUrl("file:///app/dist/renderer/index.html", allowed)).toBe(true);
    expect(isAllowedUrl("file:///app/dist/renderer/other.html", allowed)).toBe(false);
    expect(isAllowedUrl("https://evil.example.com/", allowed)).toBe(false);
    expect(isAllowedUrl("javascript:alert(1)", allowed)).toBe(false);
    expect(isAllowedUrl("not a url", allowed)).toBe(false);
  });

  it("denies window.open and blocks navigation and webviews on a web contents", async () => {
    const { guardWebContents } = await import("../src/main/window.js");
    const handlers = new Map<string, (...args: unknown[]) => void>();
    let openHandler: (() => { action: string }) | undefined;
    const contents = {
      setWindowOpenHandler: (handler: () => { action: string }) => {
        openHandler = handler;
      },
      on: (name: string, handler: (...args: unknown[]) => void) => {
        handlers.set(name, handler);
      },
    };
    guardWebContents(contents as never, "file:///app/dist/renderer/index.html");
    expect(openHandler?.()).toEqual({ action: "deny" });

    const prevented: string[] = [];
    const eventFor = (name: string) => ({ preventDefault: () => prevented.push(name) });
    handlers.get("will-navigate")?.(eventFor("navigate"), "https://evil.example.com");
    handlers.get("will-redirect")?.(eventFor("redirect"), "https://evil.example.com");
    handlers.get("will-frame-navigate")?.({ url: "https://evil.example.com", preventDefault: () => prevented.push("frame") });
    handlers.get("will-attach-webview")?.(eventFor("webview"));
    handlers.get("will-navigate")?.(eventFor("own"), "file:///app/dist/renderer/index.html");
    expect(prevented).toEqual(["navigate", "redirect", "frame", "webview"]);
  });
});

describe("parseHeadlessArgs", () => {
  it("parses the documented end-to-end command line", () => {
    const parsed = parseHeadlessArgs(["--server", "http://127.0.0.1:8000", "--code", "KQ7M-2XBV", "--platform", "simulated"]);
    expect(parsed).toEqual({
      ok: true,
      args: {
        server: "http://127.0.0.1:8000",
        code: "KQ7M-2XBV",
        platform: "simulated",
        deviceName: undefined,
        dataDir: undefined,
        runSeconds: 0,
        verbose: false,
        help: false,
      },
    });
  });

  it("accepts --flag=value and the optional settings", () => {
    const parsed = parseHeadlessArgs(["--server=http://localhost:8000", "--run-seconds=30", "--verbose", "--device-name", "Test"]);
    expect(parsed.ok && parsed.args).toMatchObject({ server: "http://localhost:8000", runSeconds: 30, verbose: true, deviceName: "Test" });
  });

  it("rejects an unknown option, a missing value, a bad platform and a bad duration", () => {
    expect(parseHeadlessArgs(["--shell", "cmd"])).toMatchObject({ ok: false });
    expect(parseHeadlessArgs(["--server"])).toMatchObject({ ok: false });
    expect(parseHeadlessArgs(["--server", "--code", "X"])).toMatchObject({ ok: false });
    expect(parseHeadlessArgs(["--platform", "linux"])).toMatchObject({ ok: false });
    expect(parseHeadlessArgs(["--run-seconds", "-1"])).toMatchObject({ ok: false });
    expect(parseHeadlessArgs(["--run-seconds", "1.5"])).toMatchObject({ ok: false });
  });
});
