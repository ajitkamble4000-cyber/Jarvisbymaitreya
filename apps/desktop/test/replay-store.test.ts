import * as fs from "node:fs";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Agent } from "../src/agent/agent.js";
import { verifyCommand, type VerifyContext } from "../src/agent/command-verifier.js";
import { configFilePath, saveConfig } from "../src/agent/config.js";
import { createLogger, type Logger } from "../src/agent/logger.js";
import { normaliseServerUrl } from "../src/agent/server-url.js";
import {
  REPLAY_STORE_FILE_NAME,
  REPLAY_STORE_MAX_ENTRIES,
  REPLAY_WINDOW_MS,
  ReplayStore,
  ReplayStoreError,
  replayStoreFileIn,
} from "../src/agent/replay-store.js";
import { DEVICE_SECRET_NAME, MemorySecretStore } from "../src/agent/secret-store.js";
import { SimulatedPlatform } from "../src/platform/simulated.js";
import { startFakeBackend, type FakeBackend } from "./fake-backend.js";
import { fixtureConfig, removeDir, signedCommandText, sleep, tempDir, TEST_SECRET, waitFor, WS_PATH } from "./helpers.js";

const NOW = Date.parse("2026-10-19T04:51:40Z");
const DEVICE_ID = "11111111-2222-4333-8444-555555555555";

interface Captured {
  logger: Logger;
  lines: string[];
}

function capture(): Captured {
  const lines: string[] = [];
  return { logger: createLogger((_level, line) => lines.push(line)), lines };
}

/** A clock the test moves by hand. */
function movableClock(start: number): { clock: () => number; set: (ms: number) => void } {
  let current = start;
  return { clock: () => current, set: (ms: number) => (current = ms) };
}

function verifyContext(replay: VerifyContext["replay"], nowMs: number): VerifyContext {
  return { secret: TEST_SECRET, config: fixtureConfig(), nowMs, replay };
}

function storeFile(dir: string): string {
  return replayStoreFileIn(dir);
}

function readEntries(dir: string): Array<{ id: string; nonce: string; seen_at: number }> {
  const parsed = JSON.parse(fs.readFileSync(storeFile(dir), "utf8")) as { entries: Array<{ id: string; nonce: string; seen_at: number }> };
  return parsed.entries;
}

describe("ReplayStore: loading", () => {
  let dir: string;

  beforeEach(() => {
    dir = tempDir("jarvis-replay-load-");
  });

  afterEach(() => {
    removeDir(dir);
  });

  it("starts empty when the file is missing, and does not write on open", async () => {
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    expect(store.degraded).toBe(false);
    expect(store.cache.size()).toBe(0);
    expect(store.cache.issuedFloorMs).toBeNull();
    expect(fs.existsSync(storeFile(dir))).toBe(false);
  });

  it("the file name is replay-store.json in the data directory", () => {
    expect(REPLAY_STORE_FILE_NAME).toBe("replay-store.json");
    expect(storeFile(dir)).toBe(path.join(dir, "replay-store.json"));
  });

  it("uses a 10 minute window and a 10,000 entry limit", () => {
    expect(REPLAY_WINDOW_MS).toBe(600_000);
    expect(REPLAY_STORE_MAX_ENTRIES).toBe(10_000);
  });

  it("a corrupt file is not started empty: the store is degraded and refuses commands issued before start-up", async () => {
    fs.writeFileSync(storeFile(dir), "{ not json", "utf8");
    const { logger, lines } = capture();
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW, logger });
    expect(store.degraded).toBe(true);
    expect(store.cache.issuedFloorMs).toBe(NOW);
    expect(lines.some((line) => line.includes("WARN") && line.includes("degraded until the file is recreated"))).toBe(true);

    // The floor is the start-up time itself. A command issued 30 s before start-up may have run before the crash.
    const old = signedCommandText({ id: "old-1", nonce: "old-n1", issuedAtMs: NOW - 61_000, expiresAtMs: NOW + 60_000 }, NOW);
    const recent = signedCommandText({ id: "recent-1", nonce: "recent-n1", issuedAtMs: NOW - 30_000, expiresAtMs: NOW + 60_000 }, NOW);
    const fresh = signedCommandText({ id: "fresh-1", nonce: "fresh-n1", issuedAtMs: NOW + 1_000, expiresAtMs: NOW + 60_000 }, NOW);
    expect(verifyCommand(old, verifyContext(store.cache, NOW))).toMatchObject({ ok: false, code: "replayed" });
    expect(verifyCommand(recent, verifyContext(store.cache, NOW))).toMatchObject({ ok: false, code: "replayed" });
    expect(verifyCommand(fresh, verifyContext(store.cache, NOW))).toMatchObject({ ok: true });
    // Opening never rewrites the corrupt file, so the evidence is kept until a command is recorded.
    expect(fs.readFileSync(storeFile(dir), "utf8")).toBe("{ not json");
  });

  it("a structurally invalid file is corrupt too", async () => {
    fs.writeFileSync(storeFile(dir), JSON.stringify({ version: 1, entries: [{ id: "", nonce: "x", seen_at: 1 }] }), "utf8");
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    expect(store.degraded).toBe(true);
  });

  it("a file with an unknown version is corrupt too", async () => {
    fs.writeFileSync(storeFile(dir), JSON.stringify({ version: 2, entries: [] }), "utf8");
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    expect(store.degraded).toBe(true);
  });

  it("an unreadable file (a directory at the path) is degraded, not empty", async () => {
    fs.mkdirSync(storeFile(dir));
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    expect(store.degraded).toBe(true);
    expect(store.cache.issuedFloorMs).toBe(NOW);
  });

  it("a degraded store becomes healthy once a write recreates the file", async () => {
    fs.writeFileSync(storeFile(dir), "garbage", "utf8");
    const { logger, lines } = capture();
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW, logger });
    expect(store.degraded).toBe(true);

    store.cache.remember({ id: "after-1", nonce: "after-nonce-secret" }, NOW);
    await store.persist();

    expect(store.degraded).toBe(false);
    expect(store.cache.issuedFloorMs).toBe(NOW);
    expect(readEntries(dir)).toEqual([{ id: "after-1", nonce: "after-nonce-secret", seen_at: NOW }]);
    expect(lines.some((line) => line.includes("INFO") && line.includes("recreated"))).toBe(true);
    // Log lines never carry the nonce.
    expect(lines.join("\n")).not.toContain("after-nonce-secret");
  });

  it("keeps only the newest entries when the file holds more than the limit, and warns", async () => {
    const entries = Array.from({ length: REPLAY_STORE_MAX_ENTRIES + 1 }, (_, index) => ({
      id: `cmd-${index}`,
      nonce: `nonce-${index}`,
      seen_at: NOW - 1_000 + index,
    }));
    fs.writeFileSync(storeFile(dir), JSON.stringify({ version: 1, entries }), "utf8");
    const { logger, lines } = capture();
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW, logger });
    expect(store.degraded).toBe(false);
    expect(store.cache.size()).toBe(REPLAY_STORE_MAX_ENTRIES);
    expect(store.cache.hasId("cmd-0", NOW)).toBe(false);
    expect(store.cache.hasId("cmd-1", NOW)).toBe(true);
    expect(lines.some((line) => line.includes("WARN") && line.includes("entry limit"))).toBe(true);
  });

  it("drops records older than the window when loading", async () => {
    fs.writeFileSync(
      storeFile(dir),
      JSON.stringify({
        version: 1,
        entries: [
          { id: "stale", nonce: "stale-n", seen_at: NOW - REPLAY_WINDOW_MS - 1 },
          { id: "fresh", nonce: "fresh-n", seen_at: NOW - 1_000 },
        ],
      }),
      "utf8",
    );
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    expect(store.cache.hasId("stale", NOW)).toBe(false);
    expect(store.cache.hasId("fresh", NOW)).toBe(true);
  });
});

describe("ReplayStore: writing", () => {
  let dir: string;

  beforeEach(() => {
    dir = tempDir("jarvis-replay-write-");
  });

  afterEach(() => {
    removeDir(dir);
  });

  it("writes {version, entries} with id, nonce and seen_at, and leaves no temporary file behind", async () => {
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    store.cache.remember({ id: "cmd-w-1", nonce: "nonce-w-1" }, NOW);
    await store.persist();
    expect(readEntries(dir)).toEqual([{ id: "cmd-w-1", nonce: "nonce-w-1", seen_at: NOW }]);
    expect(JSON.parse(fs.readFileSync(storeFile(dir), "utf8"))).toMatchObject({ version: 1 });
    expect(fs.readdirSync(dir)).toEqual([REPLAY_STORE_FILE_NAME]);
  });

  it.skipIf(process.platform === "win32")("keeps the file owner-only on POSIX", async () => {
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    store.cache.remember({ id: "cmd-mode", nonce: "nonce-mode" }, NOW);
    await store.persist();
    expect(fs.statSync(storeFile(dir)).mode & 0o777).toBe(0o600);
  });

  it("prunes records older than the window on every write", async () => {
    const time = movableClock(NOW);
    const store = await ReplayStore.open({ file: storeFile(dir), clock: time.clock });
    store.cache.remember({ id: "early", nonce: "early-n" }, NOW);
    await store.persist();
    expect(readEntries(dir)).toHaveLength(1);

    time.set(NOW + REPLAY_WINDOW_MS + 1);
    store.cache.remember({ id: "late", nonce: "late-n" }, NOW + REPLAY_WINDOW_MS + 1);
    await store.persist();
    expect(readEntries(dir).map((entry) => entry.id)).toEqual(["late"]);
  });

  it("drops the oldest entries beyond the limit and warns", async () => {
    const { logger, lines } = capture();
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW, logger, maxEntries: 2 });
    store.cache.remember({ id: "a", nonce: "na" }, NOW);
    store.cache.remember({ id: "b", nonce: "nb" }, NOW + 1);
    store.cache.remember({ id: "c", nonce: "nc" }, NOW + 2);
    await store.persist();
    expect(readEntries(dir).map((entry) => entry.id)).toEqual(["b", "c"]);
    expect(lines.some((line) => line.includes("WARN") && line.includes("entry limit"))).toBe(true);
  });

  it("a second store instance on the same file rejects a command the first one recorded", async () => {
    const first = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    const text = signedCommandText({ id: "across-1", nonce: "across-nonce-1", issuedAtMs: NOW, expiresAtMs: NOW + 60_000 }, NOW);
    expect(verifyCommand(text, verifyContext(first.cache, NOW)).ok).toBe(true);
    await first.persist();

    const restarted = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW + 5_000 });
    expect(verifyCommand(text, verifyContext(restarted.cache, NOW + 5_000))).toMatchObject({ ok: false, code: "replayed" });

    // A new id under the same nonce is also refused after the restart.
    const sameNonce = signedCommandText({ id: "across-2", nonce: "across-nonce-1", issuedAtMs: NOW, expiresAtMs: NOW + 60_000 }, NOW);
    expect(verifyCommand(sameNonce, verifyContext(restarted.cache, NOW + 5_000))).toMatchObject({ ok: false, code: "replayed" });
  });

  it("a failed write is reported, and a healthy store stays healthy", async () => {
    const sub = path.join(dir, "sub");
    const store = await ReplayStore.open({ file: path.join(sub, REPLAY_STORE_FILE_NAME), clock: () => NOW });
    // Put a regular file where the directory must be, so the write cannot create its temporary file.
    fs.writeFileSync(sub, "not a directory", "utf8");
    store.cache.remember({ id: "fail-1", nonce: "fail-n" }, NOW);
    await expect(store.persist()).rejects.toBeInstanceOf(ReplayStoreError);
    expect(store.degraded).toBe(false);
  });

  it("a degraded store stays degraded when a write fails", async () => {
    const blocker = path.join(dir, "blocker");
    fs.writeFileSync(blocker, "x", "utf8");
    const store = await ReplayStore.open({ file: path.join(blocker, REPLAY_STORE_FILE_NAME), clock: () => NOW });
    expect(store.degraded).toBe(true);
    store.cache.remember({ id: "still-1", nonce: "still-n" }, NOW);
    await expect(store.persist()).rejects.toThrow(/could not be written/);
    expect(store.degraded).toBe(true);
    expect(store.cache.issuedFloorMs).toBe(NOW);
  });

  it("a write does not lift the degraded floor: a command issued before start-up stays refused", async () => {
    fs.writeFileSync(storeFile(dir), "garbage", "utf8");
    const store = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    store.cache.remember({ id: "after-2", nonce: "after-n2" }, NOW);
    await store.persist();

    expect(store.degraded).toBe(false);
    const before = signedCommandText({ id: "before-1", nonce: "before-n1", issuedAtMs: NOW - 30_000, expiresAtMs: NOW + 60_000 }, NOW);
    expect(verifyCommand(before, verifyContext(store.cache, NOW))).toMatchObject({ ok: false, code: "replayed" });
  });

  it("the floor is written to the file, survives a restart, and ends one replay window after start-up", async () => {
    fs.writeFileSync(storeFile(dir), "garbage", "utf8");
    const first = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    first.cache.remember({ id: "after-3", nonce: "after-n3" }, NOW);
    await first.persist();

    // The file is valid again after the restart, yet the floor it recorded still applies.
    const later = NOW + 5_000;
    const restarted = await ReplayStore.open({ file: storeFile(dir), clock: () => later });
    expect(restarted.degraded).toBe(false);
    expect(restarted.cache.issuedFloorAt(later)).toBe(NOW);
    const before = signedCommandText({ id: "before-2", nonce: "before-n2", issuedAtMs: NOW - 30_000, expiresAtMs: NOW + 60_000 }, later);
    expect(verifyCommand(before, verifyContext(restarted.cache, later))).toMatchObject({ ok: false, code: "replayed" });
    // Every command issued before the floor has expired one window later, so the floor no longer applies.
    expect(restarted.cache.issuedFloorAt(NOW + REPLAY_WINDOW_MS)).toBeNull();
  });

  it("a paired device with no replay file is degraded, not a first start", async () => {
    const paired = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW, expectExisting: true });
    expect(paired.degraded).toBe(true);
    expect(paired.cache.issuedFloorMs).toBe(NOW);

    const unpaired = await ReplayStore.open({ file: storeFile(dir), clock: () => NOW });
    expect(unpaired.degraded).toBe(false);
  });
});

describe("Agent with a replay store (fake backend, simulated platform)", () => {
  let backend: FakeBackend;
  let dir: string;
  let agents: Agent[];

  beforeEach(async () => {
    backend = await startFakeBackend({ heartbeatSeconds: 1 });
    dir = tempDir("jarvis-replay-agent-");
    agents = [];
  });

  afterEach(async () => {
    for (const agent of agents) {
      await agent.stop();
    }
    vi.restoreAllMocks();
    await backend.close();
    removeDir(dir);
  });

  /** An agent that is already paired on disk, so it connects at start without a pairing call. */
  async function pairedAgent(platform: SimulatedPlatform, secrets: MemorySecretStore): Promise<Agent> {
    const origin = normaliseServerUrl(backend.url).origin;
    await saveConfig(configFilePath(dir), {
      ...fixtureConfig(),
      pairing: {
        deviceId: DEVICE_ID,
        deviceName: "Replay test PC",
        serverUrl: origin,
        websocketPath: WS_PATH,
        heartbeatIntervalSeconds: 1,
        protocolVersion: 1,
        pairedAt: new Date(NOW).toISOString(),
      },
    });
    await secrets.set(DEVICE_SECRET_NAME, TEST_SECRET);
    const agent = new Agent({
      dataDir: dir,
      platform,
      secretStore: secrets,
      backoff: { baseMs: 20, maxMs: 200, random: () => 0 },
    });
    agents.push(agent);
    return agent;
  }

  const spawns = (platform: SimulatedPlatform): number =>
    platform.calls.filter((call) => call.method === "spawnDetached").length;

  it("a restarted agent with the same data directory rejects a command it already executed", async () => {
    const secrets = new MemorySecretStore();
    const firstPlatform = new SimulatedPlatform({ firstPid: 4312 });
    const first = await pairedAgent(firstPlatform, secrets);
    await first.init();
    await first.start();
    await waitFor(() => first.getStatus().state === "connected");

    const frame = signedCommandText({ id: "restart-1", args: { app_id: "chrome" }, nonce: "restart-nonce-1" }, Date.now());
    backend.connections[0]?.socket.send(frame);
    await waitFor(() => backend.framesOf("result").length === 1);
    expect(spawns(firstPlatform)).toBe(1);
    await first.stop();

    const secondPlatform = new SimulatedPlatform({ firstPid: 9000 });
    const second = await pairedAgent(secondPlatform, secrets);
    await second.init();
    await second.start();
    await waitFor(() => second.getStatus().state === "connected");

    // Byte-for-byte the same frame, sent to the restarted agent.
    backend.connections[1]?.socket.send(frame);
    await sleep(200);
    expect(spawns(secondPlatform)).toBe(0);
    expect(fs.readFileSync(storeFile(dir), "utf8")).toContain("restart-1");
  });

  it("a corrupt replay store makes the restarted agent refuse an old command, and still run a fresh one", async () => {
    fs.writeFileSync(storeFile(dir), "{ corrupt", "utf8");
    const secrets = new MemorySecretStore();
    const platform = new SimulatedPlatform({ firstPid: 4312 });
    const agent = await pairedAgent(platform, secrets);
    await agent.init();
    await agent.start();
    await waitFor(() => agent.getStatus().state === "connected");

    const now = Date.now();
    const old = signedCommandText(
      { id: "degraded-old", args: { app_id: "chrome" }, nonce: "degraded-old-n", issuedAtMs: now - 120_000, expiresAtMs: now + 300_000 },
      now,
    );
    backend.connections[0]?.socket.send(old);
    backend.connections[0]?.socket.send(signedCommandText({ id: "degraded-fresh", args: { app_id: "chrome" } }, Date.now()));
    await waitFor(() => backend.framesOf("result").some((frame) => frame.command_id === "degraded-fresh"));

    expect(backend.framesOf("result").some((frame) => frame.command_id === "degraded-old")).toBe(false);
    expect(spawns(platform)).toBe(1);
    // The fresh command's record recreated the file, so the store is healthy again.
    expect(readEntries(dir).map((entry) => entry.id)).toEqual(["degraded-fresh"]);
  });

  it("does not run a command whose replay record cannot be written, and reports it as agent_error", async () => {
    const secrets = new MemorySecretStore();
    const platform = new SimulatedPlatform({ firstPid: 4312 });
    const agent = await pairedAgent(platform, secrets);
    await agent.init();
    await agent.start();
    await waitFor(() => agent.getStatus().state === "connected");

    vi.spyOn(ReplayStore.prototype, "persist").mockRejectedValue(new ReplayStoreError("The replay store could not be written."));

    backend.connections[0]?.socket.send(signedCommandText({ id: "unrecorded-1", args: { app_id: "chrome" } }, Date.now()));
    await waitFor(() => backend.framesOf("result").some((frame) => frame.command_id === "unrecorded-1"));
    const result = backend.framesOf("result").find((frame) => frame.command_id === "unrecorded-1");
    expect(result).toMatchObject({ status: "failed", error: { code: "agent_error" } });
    expect(spawns(platform)).toBe(0);
  });
});
