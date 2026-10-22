import { EventEmitter } from "node:events";
import * as fs from "node:fs";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  expandCandidatePaths,
  WindowsPlatform,
  type SpawnFunction,
} from "../src/platform/windows.js";
import { PlatformError } from "../src/platform/platform.js";
import { removeDir, tempDir } from "./helpers.js";

const FAKE_ENV = {
  ProgramFiles: "C:\\Program Files",
  "ProgramFiles(x86)": "C:\\Program Files (x86)",
  LOCALAPPDATA: "C:\\Users\\alex\\AppData\\Local",
  SystemRoot: "C:\\Windows",
};

const CHROME_TEMPLATES = [
  "%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe",
  "%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe",
  "%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe",
];

describe("expandCandidatePaths (pure, fake environment)", () => {
  it("builds absolute Chrome candidates from the three install variables", () => {
    expect(expandCandidatePaths(CHROME_TEMPLATES, FAKE_ENV)).toEqual([
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
      "C:\\Users\\alex\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe",
    ]);
  });

  it("builds VS Code candidates, including the per-user Programs folder", () => {
    const templates = [
      "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
      "%ProgramFiles%\\Microsoft VS Code\\Code.exe",
    ];
    expect(expandCandidatePaths(templates, FAKE_ENV)).toEqual([
      "C:\\Users\\alex\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
      "C:\\Program Files\\Microsoft VS Code\\Code.exe",
    ]);
  });

  it("drops a template whose variable is missing instead of guessing", () => {
    expect(expandCandidatePaths(CHROME_TEMPLATES, { ProgramFiles: "C:\\Program Files" })).toEqual([
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ]);
  });

  it("matches variable names case-insensitively, as Windows does", () => {
    expect(expandCandidatePaths(["%programfiles%\\App\\app.exe"], FAKE_ENV)).toEqual([
      "C:\\Program Files\\App\\app.exe",
    ]);
  });

  it("drops relative results, empty variables and paths with control characters", () => {
    const templates = ["relative\\app.exe", "%EMPTY%\\app.exe", "%ProgramFiles%\\bad\u0007.exe", "%ProgramFiles%\\ok.exe"];
    expect(expandCandidatePaths(templates, { ...FAKE_ENV, EMPTY: "" })).toEqual(["C:\\Program Files\\ok.exe"]);
  });

  it("removes duplicates after normalisation", () => {
    const templates = ["%ProgramFiles%\\A\\a.exe", "%ProgramFiles%\\A\\..\\A\\a.exe"];
    expect(expandCandidatePaths(templates, FAKE_ENV)).toEqual(["C:\\Program Files\\A\\a.exe"]);
  });

  it("never uses PATH: a bare executable name is not a candidate", () => {
    expect(expandCandidatePaths(["chrome.exe"], { PATH: "C:\\Tools" })).toEqual([]);
  });
});

/** A child_process stand-in. No process is started; the test drives the events. */
class FakeChild extends EventEmitter {
  pid: number | undefined = 4312;
  killed = false;
  kill(): boolean {
    this.killed = true;
    return true;
  }
  unref(): void {
    // no-op: the real child is detached
  }
}

interface SpawnCall {
  executable: string;
  args: readonly string[];
  options: Record<string, unknown>;
}

function fakeSpawner(configure: (child: FakeChild) => void): { spawnImpl: SpawnFunction; calls: SpawnCall[]; child: () => FakeChild | undefined } {
  const calls: SpawnCall[] = [];
  let last: FakeChild | undefined;
  const spawnImpl: SpawnFunction = (executable, args, options) => {
    calls.push({ executable, args, options: options as unknown as Record<string, unknown> });
    const child = new FakeChild();
    last = child;
    queueMicrotask(() => configure(child));
    return child as unknown as ReturnType<SpawnFunction>;
  };
  return { spawnImpl, calls, child: () => last };
}

describe("WindowsPlatform.spawnDetached start rules (fake child process)", () => {
  it("starts with shell false, windowsHide, detached and stdio ignore, then reports the pid", async () => {
    const started: number[] = [];
    const spawner = fakeSpawner((child) => child.emit("spawn"));
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 20 });
    const result = await platform.spawnDetached({
      executable: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      args: [],
      signal: new AbortController().signal,
      onStarted: (pid) => started.push(pid),
    });
    expect(result).toEqual({ pid: 4312 });
    expect(started).toEqual([4312]);
    expect(spawner.calls[0]?.options).toMatchObject({ shell: false, windowsHide: true, detached: true, stdio: "ignore" });
    expect(spawner.calls[0]?.args).toEqual([]);
  });

  it("passes project paths as a separate argument, never through a shell", async () => {
    const spawner = fakeSpawner((child) => child.emit("spawn"));
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 5 });
    await platform.spawnDetached({
      executable: "C:\\Code.exe",
      args: ["C:\\Projects\\a & b; del *"],
      signal: new AbortController().signal,
      onStarted: () => undefined,
    });
    expect(spawner.calls[0]?.args).toEqual(["C:\\Projects\\a & b; del *"]);
    expect(spawner.calls[0]?.options.shell).toBe(false);
  });

  it("reports not_installed when the executable does not exist (ENOENT before a pid)", async () => {
    const spawner = fakeSpawner((child) => {
      const error = Object.assign(new Error("spawn missing ENOENT"), { code: "ENOENT" });
      child.emit("error", error);
    });
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 5 });
    const started: number[] = [];
    const attempt = platform.spawnDetached({
      executable: "C:\\nope.exe",
      args: [],
      signal: new AbortController().signal,
      onStarted: (pid) => started.push(pid),
    });
    await expect(attempt).rejects.toMatchObject({ code: "not_installed" });
    expect(started).toEqual([]);
  });

  it("reports execution_failed when the process exits with an error during the check window", async () => {
    const spawner = fakeSpawner((child) => {
      child.emit("spawn");
      child.emit("exit", 1);
    });
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 50 });
    await expect(
      platform.spawnDetached({ executable: "C:\\bad.exe", args: [], signal: new AbortController().signal, onStarted: () => undefined }),
    ).rejects.toMatchObject({ code: "execution_failed" });
  });

  it("treats a clean early exit (code 0, for example a handoff to a running instance) as started", async () => {
    const spawner = fakeSpawner((child) => {
      child.emit("spawn");
      child.emit("exit", 0);
    });
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 10 });
    await expect(
      platform.spawnDetached({ executable: "C:\\ok.exe", args: [], signal: new AbortController().signal, onStarted: () => undefined }),
    ).resolves.toEqual({ pid: 4312 });
  });

  it("does not treat an Explorer exit code as a failed folder open (explorer hands off and exits non-zero)", async () => {
    const spawner = fakeSpawner((child) => {
      child.emit("spawn");
      child.emit("exit", 1);
    });
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 50 });
    await expect(platform.openFolder("C:\\Projects")).resolves.toBeUndefined();
    expect(spawner.calls[0]?.args).toEqual(["C:\\Projects"]);
  });

  it("cancels before the pid exists: the child is killed and the start rejects as cancelled", async () => {
    const spawner = fakeSpawner(() => undefined);
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 5 });
    const controller = new AbortController();
    const attempt = platform.spawnDetached({
      executable: "C:\\slow.exe",
      args: [],
      signal: controller.signal,
      onStarted: () => undefined,
    });
    controller.abort();
    await expect(attempt).rejects.toMatchObject({ code: "cancelled" });
    expect(spawner.child()?.killed).toBe(true);
  });

  it("does not start at all when the signal is already aborted", async () => {
    const spawner = fakeSpawner((child) => child.emit("spawn"));
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl });
    const controller = new AbortController();
    controller.abort();
    await expect(
      platform.spawnDetached({ executable: "C:\\x.exe", args: [], signal: controller.signal, onStarted: () => undefined }),
    ).rejects.toBeInstanceOf(PlatformError);
    expect(spawner.calls).toHaveLength(0);
  });

  it("ignores cancellation after the pid exists", async () => {
    const controller = new AbortController();
    const spawner = fakeSpawner((child) => {
      child.emit("spawn");
    });
    const platform = new WindowsPlatform({ env: FAKE_ENV, spawnImpl: spawner.spawnImpl, startCheckMs: 30 });
    const attempt = platform.spawnDetached({
      executable: "C:\\started.exe",
      args: [],
      signal: controller.signal,
      onStarted: () => controller.abort(),
    });
    await expect(attempt).resolves.toEqual({ pid: 4312 });
    expect(spawner.child()?.killed).toBe(false);
  });
});

describe("WindowsPlatform local file, folder and URL rules", () => {
  let dir: string;

  beforeEach(() => {
    dir = tempDir("jarvis-win-");
  });

  afterEach(() => {
    removeDir(dir);
  });

  it("reads a small file completely and not truncated", async () => {
    const file = path.join(dir, "notes.txt");
    fs.writeFileSync(file, "hello notes", "utf8");
    const platform = new WindowsPlatform({ env: FAKE_ENV });
    await expect(platform.readFileLimited(file, 64 * 1024)).resolves.toEqual({
      content: "hello notes",
      truncated: false,
      sizeBytes: 11,
    });
  });

  it("reads at most the limit and flags truncation for a larger file", async () => {
    const file = path.join(dir, "big.txt");
    fs.writeFileSync(file, "a".repeat(70_000), "utf8");
    const platform = new WindowsPlatform({ env: FAKE_ENV });
    const result = await platform.readFileLimited(file, 64 * 1024);
    expect(result.truncated).toBe(true);
    expect(result.sizeBytes).toBe(70_000);
    expect(Buffer.byteLength(result.content, "utf8")).toBe(64 * 1024);
  });

  it.skipIf(process.platform === "win32")("refuses to read a symbolic link", async () => {
    const target = path.join(dir, "real.txt");
    const link = path.join(dir, "link.txt");
    fs.writeFileSync(target, "secret", "utf8");
    fs.symlinkSync(target, link);
    const platform = new WindowsPlatform({ env: FAKE_ENV });
    await expect(platform.readFileLimited(link, 1024)).rejects.toMatchObject({ code: "permission_denied" });
  });

  it("reports a folder that exists and one that does not", async () => {
    const platform = new WindowsPlatform({ env: FAKE_ENV });
    await expect(platform.folderExists(dir)).resolves.toBe(true);
    await expect(platform.folderExists(path.join(dir, "missing"))).resolves.toBe(false);
  });

  it("only opens http and https URLs, and passes the URL to the injected host opener", async () => {
    const opened: string[] = [];
    const platform = new WindowsPlatform({
      env: FAKE_ENV,
      openExternal: async (url) => {
        opened.push(url);
      },
    });
    await platform.openExternal("https://www.youtube.com");
    expect(opened).toEqual(["https://www.youtube.com"]);
    await expect(platform.openExternal("file:///C:/Windows/System32/calc.exe")).rejects.toMatchObject({
      code: "execution_failed",
    });
    await expect(platform.openExternal("javascript:alert(1)")).rejects.toBeInstanceOf(PlatformError);
    expect(opened).toHaveLength(1);
  });

  it("reports system information with the documented fields", () => {
    const info = new WindowsPlatform({ env: FAKE_ENV }).systemInfo();
    expect(Object.keys(info).sort()).toEqual(
      ["arch", "cpu_count", "free_memory_mb", "hostname", "os", "os_version", "total_memory_mb", "uptime_seconds"].sort(),
    );
    expect(info.os).toBe("windows");
  });
});
