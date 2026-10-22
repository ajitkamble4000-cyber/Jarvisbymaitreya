import * as fs from "node:fs";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Outbox } from "../src/agent/outbox.js";
import {
  ConfigError,
  configFilePath,
  defaultConfig,
  enabledCapabilities,
  loadConfig,
  parseConfig,
  resourcesForServer,
  saveConfig,
} from "../src/agent/config.js";
import { FileSecretStore, MemorySecretStore, SafeStorageSecretStore, type SafeStorageLike } from "../src/agent/secret-store.js";
import type { AgentResult } from "../src/shared/protocol.js";
import { fixtureConfig, removeDir, tempDir } from "./helpers.js";

const POSIX = process.platform !== "win32";

function result(commandId: string, status: "succeeded" | "failed" = "succeeded"): AgentResult {
  return {
    type: "result",
    command_id: commandId,
    status,
    output: status === "succeeded" ? { pid: 4000, app_id: "chrome", verified: false, summary: "Started." } : {},
    error: status === "failed" ? { code: "not_installed", message: "Not installed.", details: {} } : null,
    started_at: "2026-10-19T04:51:38.000Z",
    finished_at: "2026-10-19T04:51:39.000Z",
  };
}

let dir: string;

beforeEach(() => {
  dir = tempDir();
});

afterEach(() => {
  removeDir(dir);
});

describe("outbox", () => {
  it("persists a result before add() resolves and reloads it after a restart", async () => {
    const file = Outbox.fileIn(dir);
    const first = new Outbox(file);
    await first.add(result("cmd-1"));
    expect(fs.existsSync(file)).toBe(true);

    const second = new Outbox(file);
    await second.load();
    expect(second.pending().map((entry) => entry.command_id)).toEqual(["cmd-1"]);
  });

  it("keeps one entry per command id, the latest one", async () => {
    const outbox = new Outbox(Outbox.fileIn(dir));
    await outbox.add(result("cmd-2", "failed"));
    await outbox.add(result("cmd-2", "succeeded"));
    expect(outbox.size()).toBe(1);
    expect(outbox.get("cmd-2")?.status).toBe("succeeded");
  });

  it("removes acknowledged entries and keeps the rest across a reload", async () => {
    const file = Outbox.fileIn(dir);
    const outbox = new Outbox(file);
    await outbox.add(result("a"));
    await outbox.add(result("b"));
    await outbox.acknowledge(["a", "unknown"]);
    const reloaded = new Outbox(file);
    await reloaded.load();
    expect(reloaded.pending().map((entry) => entry.command_id)).toEqual(["b"]);
  });

  it("starts empty when the file is not valid JSON, and drops invalid entries", async () => {
    const file = Outbox.fileIn(dir);
    fs.writeFileSync(file, "{ not json", "utf8");
    const broken = new Outbox(file);
    await broken.load();
    expect(broken.size()).toBe(0);

    fs.writeFileSync(file, JSON.stringify({ version: 1, entries: [result("ok-1"), { command_id: 7 }] }), "utf8");
    const mixed = new Outbox(file);
    await mixed.load();
    expect(mixed.pending().map((entry) => entry.command_id)).toEqual(["ok-1"]);
  });

  it("keeps a result whose write failed in memory and persists it with the next write", async () => {
    // A regular file where the folder should be makes the write fail.
    const blocker = path.join(dir, "blocker");
    fs.writeFileSync(blocker, "not a folder", "utf8");
    const outbox = new Outbox(path.join(blocker, "outbox.json"));
    await expect(outbox.add(result("x"))).rejects.toThrow();

    fs.rmSync(blocker);
    await expect(outbox.add(result("y"))).resolves.toBeUndefined();
    const reloaded = new Outbox(path.join(blocker, "outbox.json"));
    await reloaded.load();
    expect(reloaded.pending().map((entry) => entry.command_id)).toEqual(["x", "y"]);
  });

  it.skipIf(!POSIX)("writes outbox.json owner-only", async () => {
    const file = Outbox.fileIn(dir);
    await new Outbox(file).add(result("perm"));
    expect(fs.statSync(file).mode & 0o777).toBe(0o600);
  });
});

describe("config", () => {
  it("defaults to Chrome and VS Code with the documented install-folder candidates", () => {
    const config = defaultConfig();
    expect(config.approvedApps.map((app) => app.id)).toEqual(["chrome", "vscode"]);
    expect(config.approvedApps[0]?.windows.candidates).toContain(
      "%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe",
    );
    expect(config.approvedApps[1]?.label).toBe("Visual Studio Code");
    expect(enabledCapabilities(config)).toEqual([
      "launch_app",
      "open_url",
      "open_folder",
      "open_project",
      "system_info",
      "read_file",
      "app_availability",
    ]);
  });

  it("returns defaults when agent.json does not exist yet", async () => {
    const config = await loadConfig(configFilePath(dir));
    expect(config).toEqual(defaultConfig());
  });

  it("round-trips through saveConfig and loadConfig", async () => {
    const file = configFilePath(dir);
    await saveConfig(file, fixtureConfig());
    const loaded = await loadConfig(file);
    expect(loaded.approvedFolders[0]?.id).toBe("downloads");
  });

  it.skipIf(!POSIX)("saves agent.json owner-only", async () => {
    const file = configFilePath(dir);
    await saveConfig(file, defaultConfig());
    expect(fs.statSync(file).mode & 0o777).toBe(0o600);
  });

  it("treats a missing capability key as disabled", () => {
    const raw = JSON.parse(JSON.stringify(defaultConfig())) as Record<string, unknown>;
    raw.capabilities = { launch_app: true };
    const config = parseConfig(JSON.stringify(raw));
    expect(enabledCapabilities(config)).toEqual(["launch_app"]);
  });

  it("rejects unknown top-level keys and unknown capability names", () => {
    const raw = JSON.parse(JSON.stringify(defaultConfig())) as Record<string, unknown>;
    expect(() => parseConfig(JSON.stringify({ ...raw, shell: "cmd" }))).toThrow(ConfigError);
    raw.capabilities = { run_shell: true };
    expect(() => parseConfig(JSON.stringify(raw))).toThrow(ConfigError);
  });

  it("rejects duplicate ids within a resource list", () => {
    const config = fixtureConfig();
    config.approvedFolders.push({ id: "downloads", label: "Again", path: path.resolve("/fixture/other") });
    expect(() => parseConfig(JSON.stringify(config))).toThrow(/duplicate id/);
  });

  it("rejects a non-http URL in approvedUrls", () => {
    const config = fixtureConfig();
    config.approvedUrls[0] = { id: "bad", label: "Bad", url: "file:///C:/secret.txt" };
    expect(() => parseConfig(JSON.stringify(config))).toThrow(ConfigError);
  });

  it("never echoes file contents in a validation error", () => {
    const raw = JSON.stringify({ version: 1, approvedApps: "SECRET-VALUE-XYZ" });
    try {
      parseConfig(raw);
      throw new Error("expected failure");
    } catch (error) {
      expect(String((error as Error).message)).not.toContain("SECRET-VALUE-XYZ");
    }
  });

  it("sends only ids, labels and URL strings to the server, never paths", () => {
    const resources = resourcesForServer(fixtureConfig());
    const json = JSON.stringify(resources);
    expect(json).not.toContain("/fixture");
    expect(resources.urls).toEqual([{ id: "youtube", label: "YouTube", url: "https://www.youtube.com" }]);
    expect(resources.folders).toEqual([{ id: "downloads", label: "Downloads" }]);
  });
});

describe("secret stores", () => {
  it("FileSecretStore keeps values, deletes them and stays owner-only", async () => {
    const file = path.join(dir, "secrets.json");
    const store = new FileSecretStore(file);
    await store.set("device_secret", "value-1");
    expect(await store.get("device_secret")).toBe("value-1");
    await store.delete("device_secret");
    expect(await store.get("device_secret")).toBeNull();
    await store.set("device_secret", "value-2");
    if (POSIX) {
      expect(fs.statSync(file).mode & 0o777).toBe(0o600);
    }
  });

  it("SafeStorageSecretStore stores ciphertext only and decrypts on read", async () => {
    const fakeStorage: SafeStorageLike = {
      isEncryptionAvailable: () => true,
      encryptString: (plain) => Buffer.from(`enc:${plain.split("").reverse().join("")}`, "utf8"),
      decryptString: (encrypted) => encrypted.toString("utf8").replace(/^enc:/, "").split("").reverse().join(""),
    };
    const file = path.join(dir, "secrets.bin.json");
    const store = new SafeStorageSecretStore(fakeStorage, file);
    await store.set("device_secret", "plain-secret-value");
    expect(fs.readFileSync(file, "utf8")).not.toContain("plain-secret-value");
    expect(await store.get("device_secret")).toBe("plain-secret-value");
  });

  it("SafeStorageSecretStore refuses to store when encryption is unavailable", async () => {
    const store = new SafeStorageSecretStore(
      {
        isEncryptionAvailable: () => false,
        encryptString: () => Buffer.alloc(0),
        decryptString: () => "",
      },
      path.join(dir, "secrets.bin.json"),
    );
    expect(store.isAvailable()).toBe(false);
    await expect(store.set("device_secret", "x")).rejects.toThrow(/unavailable/);
    expect(fs.existsSync(path.join(dir, "secrets.bin.json"))).toBe(false);
  });

  it("MemorySecretStore can be made unavailable for pairing tests", async () => {
    const store = new MemorySecretStore({ available: false });
    expect(store.isAvailable()).toBe(false);
    await expect(store.set("device_secret", "x")).rejects.toThrow();
  });
});

describe("log redaction", () => {
  it("drops secret-like keys and keeps error codes", async () => {
    const { redactFields } = await import("../src/agent/logger.js");
    expect(redactFields({ code: "replayed", device_secret: "x", nonce: "n", signature: "s", hasId: true })).toEqual({
      code: "replayed",
      hasId: true,
    });
  });
});
