import * as fs from "node:fs";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Agent } from "../src/agent/agent.js";
import { configFilePath, saveConfig } from "../src/agent/config.js";
import { MemorySecretStore, DEVICE_SECRET_NAME } from "../src/agent/secret-store.js";
import { Outbox } from "../src/agent/outbox.js";
import { PairingError } from "../src/agent/pairing.js";
import { SimulatedPlatform } from "../src/platform/simulated.js";
import type { AgentStatus } from "../src/shared/ipc-contract.js";
import { startFakeBackend, type FakeBackend, type FakeConnection } from "./fake-backend.js";
import {
  fixtureConfig,
  removeDir,
  signedCommandText,
  sleep,
  tempDir,
  TEST_SECRET,
  waitFor,
  WS_PATH,
} from "./helpers.js";

const PAIR_CODE = "KQ7M-2XBV";
const DEVICE_ID = "11111111-2222-4333-8444-555555555555";

function pairFetch(serverUrl: string, options: { status?: number; secret?: string } = {}): {
  fetchImpl: typeof fetch;
  bodies: Record<string, unknown>[];
} {
  const bodies: Record<string, unknown>[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    expect(String(url)).toBe(`${serverUrl}/api/v1/agent/pair`);
    bodies.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
    if ((options.status ?? 201) !== 201) {
      return new Response("{}", { status: options.status ?? 401 });
    }
    return new Response(
      JSON.stringify({
        device_id: DEVICE_ID,
        device_secret: options.secret ?? TEST_SECRET,
        websocket_path: WS_PATH,
        heartbeat_interval_seconds: 1,
        protocol_version: 1,
      }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;
  return { fetchImpl, bodies };
}

describe("Agent end to end (fake backend, simulated platform)", () => {
  let backend: FakeBackend;
  let dir: string;
  let platform: SimulatedPlatform;
  let secrets: MemorySecretStore;
  let agent: Agent | undefined;
  let statuses: AgentStatus[];

  beforeEach(async () => {
    backend = await startFakeBackend({ heartbeatSeconds: 1 });
    dir = tempDir("jarvis-agent-e2e-");
    platform = new SimulatedPlatform({ firstPid: 4312 });
    secrets = new MemorySecretStore();
    statuses = [];
    agent = undefined;
  });

  afterEach(async () => {
    if (agent !== undefined) {
      await agent.stop();
    }
    await backend.close();
    removeDir(dir);
  });

  function build(fetchImpl?: typeof fetch, secretStore = secrets): Agent {
    agent = new Agent({
      dataDir: dir,
      platform,
      secretStore,
      fetchImpl,
      backoff: { baseMs: 20, maxMs: 200, random: () => 0 },
    });
    agent.onStatusChange((status) => statuses.push(status));
    return agent;
  }

  const readFile = (name: string): string => fs.readFileSync(path.join(dir, name), "utf8");

  it("pairs, connects, runs a signed command, and reports the result", async () => {
    const { fetchImpl, bodies } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    expect(current.getStatus().state).toBe("unpaired");

    await current.pair({ serverUrl: backend.url, code: PAIR_CODE, deviceName: "Test PC" });
    await waitFor(() => current.getStatus().state === "connected");
    expect(bodies[0]).toMatchObject({ code: PAIR_CODE, device_name: "Test PC", os: "simulated" });
    expect(current.getStatus()).toMatchObject({ simulated: true, banner: expect.stringContaining("SIMULATED"), deviceName: "Test PC" });

    const hello = backend.framesOf("hello")[0];
    expect(hello).toMatchObject({ type: "hello", capabilities: expect.arrayContaining(["launch_app"]) });
    expect(JSON.stringify(hello)).not.toContain("/fixture");

    const command = signedCommandText({ id: "e2e-launch-1", args: { app_id: "chrome" } }, Date.now());
    backend.connections[0]?.socket.send(command);
    await waitFor(() => backend.framesOf("result").length === 1);
    const result = backend.framesOf("result")[0];
    expect(result).toMatchObject({ command_id: "e2e-launch-1", status: "succeeded", error: null });
    const output = result?.output as Record<string, unknown>;
    expect(output).toMatchObject({ pid: 4312, app_id: "chrome", verified: false });
    expect(String(output.summary)).toMatch(/^SIMULATED: /);
  });

  it("never writes the device secret or the pairing code to disk", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");
    backend.connections[0]?.socket.send(signedCommandText({ id: "no-leak-1", args: { app_id: "chrome" } }, Date.now()));
    await waitFor(() => backend.framesOf("result").length === 1);

    const config = readFile("agent.json");
    expect(config).toContain(DEVICE_ID);
    expect(config).not.toContain(TEST_SECRET);
    expect(config).not.toContain(PAIR_CODE);
    expect(fs.readdirSync(dir).join(" ")).not.toContain("secret");
    expect(await secrets.get(DEVICE_SECRET_NAME)).toBe(TEST_SECRET);
  });

  it("does not run the same command twice and does not send a second result for it", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");

    const frame = signedCommandText({ id: "replay-1", args: { app_id: "chrome" }, nonce: "replay-nonce-1" }, Date.now());
    backend.connections[0]?.socket.send(frame);
    await waitFor(() => backend.framesOf("result").length === 1);
    backend.connections[0]?.socket.send(frame);
    await sleep(200);

    // The replay is answered only with the stored result, byte for byte. Nothing runs twice.
    expect(platform.calls.filter((call) => call.method === "spawnDetached")).toHaveLength(1);
    const results = backend.framesOf("result");
    expect(results.length).toBeGreaterThanOrEqual(1);
    expect(new Set(results.map((frame) => JSON.stringify(frame))).size).toBe(1);
  });

  it("does not answer a forged command: its claimed id is unauthenticated, so no result is sent for it", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");

    const forged = JSON.parse(signedCommandText({ id: "forged-1", args: { app_id: "chrome" } }, Date.now())) as Record<string, unknown>;
    forged.signature = "0".repeat(64);
    backend.connections[0]?.socket.send(JSON.stringify(forged));
    // Then a valid command goes through, which proves the forged frame was handled before the check.
    backend.connections[0]?.socket.send(signedCommandText({ id: "valid-after-forged", args: { app_id: "chrome" } }, Date.now()));
    await waitFor(() => backend.framesOf("result").some((frame) => frame.command_id === "valid-after-forged"));
    expect(backend.framesOf("result").some((frame) => frame.command_id === "forged-1")).toBe(false);
    expect(platform.calls.some((call) => call.method === "spawnDetached")).toBe(true);
  });

  it("reports a command for an unapproved app as resource_not_approved", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");

    backend.connections[0]?.socket.send(signedCommandText({ id: "unknown-app-1", args: { app_id: "notepad" } }, Date.now()));
    await waitFor(() => backend.framesOf("result").length === 1);
    expect(backend.framesOf("result")[0]).toMatchObject({ error: { code: "resource_not_approved" } });
  });

  it("keeps a result that finishes while the agent is stopped, and sends it after the next connection", async () => {
    platform = new SimulatedPlatform({ firstPid: 4312, settleMs: 300 });
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");

    backend.connections[0]?.socket.send(signedCommandText({ id: "stopped-1", args: { app_id: "chrome" } }, Date.now()));
    await waitFor(() => platform.calls.some((call) => call.method === "spawnDetached"));
    await current.stop();
    expect(current.getStatus().state).toBe("paired");

    const outboxFile = Outbox.fileIn(dir);
    await waitFor(() => fs.existsSync(outboxFile) && fs.readFileSync(outboxFile, "utf8").includes("stopped-1"));
    await current.start();
    await waitFor(() => backend.framesOf("result").some((frame) => frame.command_id === "stopped-1"));
  });

  it("clears the pairing and the secret when the server revokes the device", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");

    backend.closeWith(backend.connections[0] as FakeConnection, 4403, "revoked");
    await waitFor(() => current.getStatus().state === "unpaired");
    await waitFor(() => readFile("agent.json").includes('"pairing": null'));
    expect(await secrets.get(DEVICE_SECRET_NAME)).toBeNull();
    expect(current.getConfigSummary().paired).toBe(false);
  });

  it("reports re-pair-required at start when the secret is missing", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const first = build(fetchImpl);
    await first.init();
    await first.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => first.getStatus().state === "connected");
    await first.stop();

    const emptySecrets = new MemorySecretStore();
    const second = build(undefined, emptySecrets);
    await second.init();
    expect(second.getStatus().state).toBe("re-pair-required");
    await second.start();
    expect(second.getStatus().state).toBe("re-pair-required");
    expect(backend.connections).toHaveLength(1);
  });

  it("refuses to pair when secure storage is unavailable, and does not contact the server", async () => {
    const { fetchImpl, bodies } = pairFetch(backend.url);
    const unavailable = new MemorySecretStore({ available: false });
    const current = build(fetchImpl, unavailable);
    await current.init();
    await expect(current.pair({ serverUrl: backend.url, code: PAIR_CODE })).rejects.toThrow(/Secure storage is unavailable/);
    expect(bodies).toHaveLength(0);
    expect(current.getStatus().state).toBe("unpaired");
  });

  it("keeps the agent unpaired after a rejected code, and writes nothing", async () => {
    const { fetchImpl } = pairFetch(backend.url, { status: 401 });
    const current = build(fetchImpl);
    await current.init();
    const attempt = current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await expect(attempt).rejects.toBeInstanceOf(PairingError);
    await expect(attempt).rejects.toThrow(/invalid or has expired/);
    expect(current.getStatus().state).toBe("unpaired");
    expect(fs.existsSync(configFilePath(dir))).toBe(false);
    expect(await secrets.get(DEVICE_SECRET_NAME)).toBeNull();
  });

  it("unpair() stops the connection, removes the secret and records the device as unpaired", async () => {
    const { fetchImpl } = pairFetch(backend.url);
    const current = build(fetchImpl);
    await current.init();
    await current.pair({ serverUrl: backend.url, code: PAIR_CODE });
    await waitFor(() => current.getStatus().state === "connected");
    await current.unpair();
    expect(current.getStatus().state).toBe("unpaired");
    expect(await secrets.get(DEVICE_SECRET_NAME)).toBeNull();
    expect(current.isRunning()).toBe(false);
  });

  it("reports an unreadable agent.json as an error without guessing defaults", async () => {
    fs.writeFileSync(configFilePath(dir), "{ broken", "utf8");
    const current = build();
    await current.init();
    expect(current.getStatus()).toMatchObject({ state: "error" });
    expect(current.getStatus().message).toMatch(/not valid JSON/);
  });

  it("exposes only ids, labels and the server host in the config summary", async () => {
    await saveConfig(configFilePath(dir), fixtureConfig());
    const current = build();
    await current.init();
    const summary = current.getConfigSummary();
    expect(JSON.stringify(summary)).not.toContain("/fixture");
    expect(summary.folders).toEqual([{ id: "downloads", label: "Downloads" }]);
    expect(summary.paired).toBe(false);
  });
});
