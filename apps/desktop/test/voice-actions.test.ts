/**
 * The four voice-automation actions from the Tasura brief: compose_email, web_search, launch_installed_app and
 * create_files. Pure helpers are tested directly; the actions run through the real verifier and dispatcher on the
 * simulated platform, and the file writer runs against a real temporary folder.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Dispatcher } from "../src/agent/dispatcher.js";
import { ReplayCache, verifyCommand, type VerifiedCommand } from "../src/agent/command-verifier.js";
import { SimulatedPlatform } from "../src/platform/simulated.js";
import { writeNewFiles } from "../src/platform/files.js";
import { parseStartApps } from "../src/platform/windows.js";
import { matchInstalledApp, normaliseAppName, type InstalledApp } from "../src/shared/app-matcher.js";
import { isSafeRelativePath } from "../src/shared/safe-paths.js";
import { gmailComposeUrl, searchUrl } from "../src/shared/web-links.js";
import type { AgentConfig } from "../src/agent/config.js";
import type { AgentResult } from "../src/shared/protocol.js";
import { fixtureConfig, removeDir, signedCommandText, tempDir, TEST_SECRET } from "./helpers.js";

const NOW = Date.parse("2026-11-24T05:00:00Z");

function config(): AgentConfig {
  return { ...fixtureConfig(), approvedWorkspaces: [{ id: "projects", label: "Jarvis projects", path: path.resolve("/fixture/JarvisProjects") }] };
}

function verify(action: string, args: unknown, cfg: AgentConfig = config()) {
  return verifyCommand(signedCommandText({ action, args, nonce: `n-${Math.random()}` }, NOW), {
    secret: TEST_SECRET,
    config: cfg,
    nowMs: NOW,
    replay: new ReplayCache(),
  });
}

function verified(action: string, args: unknown): VerifiedCommand {
  const result = verify(action, args);
  if (!result.ok) {
    throw new Error(`fixture rejected: ${result.code}`);
  }
  return result.command;
}

async function run(platform: SimulatedPlatform, command: VerifiedCommand): Promise<AgentResult> {
  const results: AgentResult[] = [];
  const dispatcher = new Dispatcher({ platform, onResult: async (result) => void results.push(result) });
  dispatcher.submit(command);
  await dispatcher.idle();
  const [result] = results;
  if (result === undefined) {
    throw new Error("no result");
  }
  return result;
}

describe("app name matching", () => {
  const apps: InstalledApp[] = [
    { name: "Wispr Flow", appId: "a1" },
    { name: "Spotify", appId: "a2" },
    { name: "Visual Studio Code", appId: "a3" },
    { name: "Google Chrome", appId: "a4" },
    { name: "Microsoft Teams", appId: "a5" },
    { name: "Microsoft Edge", appId: "a6" },
  ];

  it("opens Wispr Flow when speech recognition writes Whisper Flow", () => {
    expect(matchInstalledApp("open Whisper Flow", apps)).toMatchObject({ kind: "match", app: { name: "Wispr Flow" }, how: "alias" });
  });
  it("matches exact names, VS Code shorthand and close spellings", () => {
    expect(matchInstalledApp("Spotify", apps)).toMatchObject({ kind: "match", app: { appId: "a2" }, how: "exact" });
    expect(matchInstalledApp("vs code", apps)).toMatchObject({ kind: "match", app: { appId: "a3" } });
    expect(matchInstalledApp("spotfy", apps)).toMatchObject({ kind: "match", app: { appId: "a2" }, how: "close" });
  });
  it("asks when a word fits several apps, and suggests names when nothing fits", () => {
    const microsoft = matchInstalledApp("microsoft", apps);
    expect(microsoft.kind).toBe("ambiguous");
    const none = matchInstalledApp("Photoshop", apps);
    expect(none.kind).toBe("none");
    if (none.kind === "none") {
      expect(none.suggestions).toHaveLength(3);
    }
  });
  it("drops filler words", () => {
    expect(normaliseAppName("Please open the Spotify app")).toBe("spotify");
  });
});

describe("links the agent builds", () => {
  it("fills Gmail's compose window with encoded fields and a fixed host", () => {
    const url = gmailComposeUrl({ to: ["rahul.sharma@gmail.com"], cc: ["neha@example.com"], subject: "Meeting & notes", body: "Hi Rahul,\nSee you at 4 PM." });
    const parsed = new URL(url);
    expect(parsed.origin).toBe("https://mail.google.com");
    expect(parsed.searchParams.get("to")).toBe("rahul.sharma@gmail.com");
    expect(parsed.searchParams.get("cc")).toBe("neha@example.com");
    expect(parsed.searchParams.get("su")).toBe("Meeting & notes");
    expect(parsed.searchParams.get("body")).toBe("Hi Rahul,\nSee you at 4 PM.");
    expect(url).toContain("%0A");
  });
  it("builds search links for Google, YouTube and Spotify only", () => {
    expect(searchUrl("youtube", "SVIS Annual Day")).toBe("https://www.youtube.com/results?search_query=SVIS%20Annual%20Day");
    expect(new URL(searchUrl("google", "a&b")).searchParams.get("q")).toBe("a&b");
    expect(searchUrl("spotify", "lo-fi")).toBe("https://open.spotify.com/search/lo-fi");
  });
});

describe("safe relative paths", () => {
  it.each(["portfolio", "src/index.html", ".gitignore", "assets/logo final.png", "a/b/c/d/e/f/g/h"])("accepts %s", (value) => {
    expect(isSafeRelativePath(value)).toBe(true);
  });
  it.each(["", "../x", "a/../b", "./a", "C:/x", "a\\b", "/etc/passwd", ".git/hooks/pre-commit", "con.txt", "a/", "file.", "a/b/c/d/e/f/g/h/i"])(
    "refuses %s",
    (value) => {
      expect(isSafeRelativePath(value)).toBe(false);
    },
  );
});

describe("Start menu list parsing", () => {
  it("reads one object or an array and drops malformed or duplicate rows", () => {
    expect(parseStartApps('{"Name":"Spotify","AppID":"SpotifyAB.Spotify!App"}')).toEqual([{ name: "Spotify", appId: "SpotifyAB.Spotify!App" }]);
    const apps = parseStartApps(
      JSON.stringify([
        { Name: "Wispr Flow", AppID: "com.wispr.flow" },
        { Name: "Wispr Flow", AppID: "com.wispr.flow" },
        { Name: "", AppID: "x" },
        { Name: "Bad", AppID: 'a"b' },
        { nothing: true },
      ]),
    );
    expect(apps).toEqual([{ name: "Wispr Flow", appId: "com.wispr.flow" }]);
  });
});

describe("verifier for the new actions", () => {
  it("accepts a compose_email command and refuses a bad address", () => {
    expect(verify("compose_email", { to: ["rahul@gmail.com"], subject: "Hi", body: "Hello" }).ok).toBe(true);
    const bad = verify("compose_email", { to: ["not an email"], subject: "Hi", body: "Hello" });
    expect(bad.ok).toBe(false);
    if (!bad.ok) {
      expect(bad.code).toBe("invalid_arguments");
    }
  });
  it("refuses search sites the agent does not know", () => {
    expect(verify("web_search", { site: "youtube", query: "lo-fi" }).ok).toBe(true);
    expect(verify("web_search", { site: "example", query: "x" }).ok).toBe(false);
  });
  it("refuses create_files outside an approved workspace or with escaping paths", () => {
    const unknown = verify("create_files", { workspace_id: "elsewhere", folder: "p", files: [{ path: "a.txt", content: "x" }] });
    expect(unknown.ok ? "ok" : unknown.code).toBe("resource_not_approved");
    const escaping = verify("create_files", { workspace_id: "projects", folder: "../outside", files: [{ path: "a.txt", content: "x" }] });
    expect(escaping.ok ? "ok" : escaping.code).toBe("invalid_arguments");
    const duplicate = verify("create_files", {
      workspace_id: "projects",
      folder: "p",
      files: [
        { path: "A.txt", content: "x" },
        { path: "a.txt", content: "y" },
      ],
    });
    expect(duplicate.ok ? "ok" : duplicate.code).toBe("invalid_arguments");
  });
  it("accepts create_files larger than the old 8 KB command limit", () => {
    const content = "x".repeat(20_000);
    expect(verify("create_files", { workspace_id: "projects", folder: "big", files: [{ path: "big.txt", content }] }).ok).toBe(true);
  });
  it("keeps the new actions off when agent.json does not enable them", () => {
    const cfg = config();
    cfg.capabilities.compose_email = false;
    const result = verify("compose_email", { to: ["a@b.co"], subject: "s", body: "" }, cfg);
    expect(result.ok ? "ok" : result.code).toBe("permission_denied");
  });
});

describe("actions on the simulated platform", () => {
  it("compose_email opens a Gmail draft and says nothing was sent", async () => {
    const platform = new SimulatedPlatform();
    const result = await run(platform, verified("compose_email", { to: ["rahul@gmail.com"], subject: "Meeting", body: "4 PM" }));
    expect(result.status).toBe("succeeded");
    expect(result.output).toMatchObject({ sent: false, verified: false });
    expect(String(result.output.summary)).toContain("Nothing is sent until you press Send");
    expect(platform.calls.map((call) => call.method)).toContain("openExternal");
  });
  it("web_search opens the search", async () => {
    const platform = new SimulatedPlatform();
    const result = await run(platform, verified("web_search", { site: "youtube", query: "SVIS Annual Day" }));
    expect(result.status).toBe("succeeded");
    expect(String(result.output.summary)).toContain('Searched YouTube for "SVIS Annual Day"');
  });
  it("launch_installed_app starts Wispr Flow from the words Whisper Flow", async () => {
    const platform = new SimulatedPlatform();
    const result = await run(platform, verified("launch_installed_app", { name: "Whisper Flow" }));
    expect(result.status).toBe("succeeded");
    expect(result.output).toMatchObject({ app: "Wispr Flow", matched_by: "alias" });
    expect(platform.calls.map((call) => call.method)).toEqual(["listStartApps", "launchStartApp"]);
  });
  it("launch_installed_app reports suggestions when nothing matches and starts nothing", async () => {
    const platform = new SimulatedPlatform();
    const result = await run(platform, verified("launch_installed_app", { name: "Photoshop" }));
    expect(result.status).toBe("failed");
    expect(result.error?.code).toBe("not_installed");
    expect(Array.isArray(result.error?.details.suggestions)).toBe(true);
    expect(platform.calls.map((call) => call.method)).not.toContain("launchStartApp");
  });
  it("create_files writes the project and opens it in VS Code when asked", async () => {
    const platform = new SimulatedPlatform();
    const result = await run(
      platform,
      verified("create_files", {
        workspace_id: "projects",
        folder: "portfolio",
        files: [
          { path: "index.html", content: "<h1>Hi</h1>" },
          { path: "css/style.css", content: "h1{color:teal}" },
        ],
        open_in_app_id: "vscode",
      }),
    );
    expect(result.status).toBe("succeeded");
    expect(result.output).toMatchObject({ created: ["index.html", "css/style.css"], opened_in: "vscode" });
    expect(platform.calls.map((call) => call.method)).toEqual(["createFiles", "findExecutable", "spawnDetached"]);
  });
  it("create_files refuses to overwrite and reports the failure", async () => {
    const platform = new SimulatedPlatform({ existingWorkspaceFiles: ["portfolio/index.html"] });
    const result = await run(platform, verified("create_files", { workspace_id: "projects", folder: "portfolio", files: [{ path: "index.html", content: "x" }] }));
    expect(result.status).toBe("failed");
    expect(result.error?.message).toContain("already exists");
  });
});

describe("writing files to a real folder", () => {
  let root: string;
  beforeEach(() => {
    root = path.join(tempDir(), "JarvisProjects");
  });
  afterEach(() => {
    removeDir(path.dirname(root));
  });

  it("creates nested folders and files under a workspace it creates if missing", async () => {
    const result = await writeNewFiles({
      root,
      folder: "notes-app",
      files: [
        { path: "index.html", content: "<p>notes</p>" },
        { path: "src/app.js", content: "console.log('hi')\n" },
      ],
    });
    expect(result.created).toEqual(["index.html", "src/app.js"]);
    expect(fs.readFileSync(path.join(root, "notes-app", "src", "app.js"), "utf8")).toBe("console.log('hi')\n");
  });

  it("writes nothing when any target already exists", async () => {
    fs.mkdirSync(path.join(root, "app"), { recursive: true });
    fs.writeFileSync(path.join(root, "app", "b.txt"), "keep me");
    await expect(
      writeNewFiles({
        root,
        folder: "app",
        files: [
          { path: "a.txt", content: "new" },
          { path: "b.txt", content: "overwrite?" },
        ],
      }),
    ).rejects.toThrow(/already exists/);
    expect(fs.existsSync(path.join(root, "app", "a.txt"))).toBe(false);
    expect(fs.readFileSync(path.join(root, "app", "b.txt"), "utf8")).toBe("keep me");
  });

  it("refuses to write through a linked folder that points outside the workspace", async () => {
    const outside = path.join(path.dirname(root), "outside");
    fs.mkdirSync(outside, { recursive: true });
    fs.mkdirSync(root, { recursive: true });
    fs.symlinkSync(outside, path.join(root, "link"), "dir");
    await expect(writeNewFiles({ root, folder: "link/project", files: [{ path: "a.txt", content: "x" }] })).rejects.toThrow(/link/);
    expect(fs.readdirSync(outside)).toEqual([]);
  });

  it("refuses unsafe names even if called directly", async () => {
    await expect(writeNewFiles({ root, folder: "../escape", files: [{ path: "a.txt", content: "x" }] })).rejects.toThrow(/not allowed/);
    await expect(writeNewFiles({ root, folder: "ok", files: [{ path: "../a.txt", content: "x" }] })).rejects.toThrow(/not allowed/);
  });
});
