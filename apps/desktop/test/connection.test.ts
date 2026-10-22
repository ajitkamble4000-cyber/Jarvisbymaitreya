import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  AgentConnection,
  backoffDelayMs,
  BACKOFF_MAX_MS,
  type ConnectionEvent,
  type ConnectionOptions,
} from "../src/agent/connection.js";
import { Outbox } from "../src/agent/outbox.js";
import { ServerUrlError } from "../src/agent/server-url.js";
import type { AgentResult, Hello } from "../src/shared/protocol.js";
import { startFakeBackend, type FakeBackend, type FakeConnection } from "./fake-backend.js";
import { removeDir, signedCancelText, signedCommandText, sleep, tempDir, TEST_SECRET, waitFor, WS_PATH } from "./helpers.js";

function buildHello(): Hello {
  return {
    type: "hello",
    agent_version: "1.0.0",
    os: "simulated",
    os_version: "0.0.0-simulated",
    capabilities: ["launch_app", "system_info"],
    resources: {
      apps: [{ id: "chrome", label: "Google Chrome" }],
      urls: [{ id: "youtube", label: "YouTube", url: "https://www.youtube.com" }],
      folders: [],
      projects: [],
      files: [],
      checks: [],
    },
  };
}

function requireConnection(backend: FakeBackend, index: number): FakeConnection {
  const connection = backend.connections[index];
  if (connection === undefined) {
    throw new Error(`no fake connection at index ${index}`);
  }
  return connection;
}

function resultFor(commandId: string): AgentResult {
  return {
    type: "result",
    command_id: commandId,
    status: "succeeded",
    output: { pid: 4000, app_id: "chrome", verified: false, summary: "SIMULATED: Started Google Chrome (pid 4000)." },
    error: null,
    started_at: "2026-10-19T04:51:38.000Z",
    finished_at: "2026-10-19T04:51:39.000Z",
  };
}

describe("backoffDelayMs", () => {
  it("starts near one second and doubles toward the 60 second cap", () => {
    expect(backoffDelayMs(0, () => 1)).toBe(1000);
    expect(backoffDelayMs(0, () => 0)).toBe(750);
    expect(backoffDelayMs(1, () => 1)).toBe(2000);
    expect(backoffDelayMs(2, () => 1)).toBe(4000);
  });

  it("never exceeds the cap, however many attempts have failed", () => {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      for (const random of [0, 0.5, 1]) {
        const delay = backoffDelayMs(attempt, () => random);
        expect(delay).toBeLessThanOrEqual(BACKOFF_MAX_MS);
        expect(delay).toBeGreaterThan(0);
      }
    }
    expect(backoffDelayMs(20, () => 1)).toBe(BACKOFF_MAX_MS);
  });

  it("applies jitter between 75 and 100 percent of the capped value", () => {
    const low = backoffDelayMs(3, () => 0);
    const high = backoffDelayMs(3, () => 1);
    expect(low).toBe(6000);
    expect(high).toBe(8000);
  });
});

describe("AgentConnection with a fake backend", () => {
  let backend: FakeBackend;
  let dir: string;
  let outbox: Outbox;
  let events: ConnectionEvent[];
  let commands: string[];
  let cancels: string[];
  let connection: AgentConnection | undefined;

  function make(overrides: Partial<ConnectionOptions> = {}): AgentConnection {
    connection = new AgentConnection({
      serverUrl: backend.url,
      websocketPath: WS_PATH,
      secret: TEST_SECRET,
      buildHello,
      outbox,
      runningCommandIds: () => [],
      onCommandFrame: (text) => {
        commands.push(text);
      },
      onCancelFrame: (text) => {
        cancels.push(text);
      },
      onEvent: (event) => {
        events.push(event);
      },
      backoff: { baseMs: 20, maxMs: 200, random: () => 0 },
      ...overrides,
    });
    return connection;
  }

  const kinds = (): string[] => events.map((event) => event.kind);

  beforeEach(async () => {
    backend = await startFakeBackend({ heartbeatSeconds: 1 });
    dir = tempDir("jarvis-conn-");
    outbox = new Outbox(Outbox.fileIn(dir));
    events = [];
    commands = [];
    cancels = [];
    connection = undefined;
  });

  afterEach(async () => {
    if (connection !== undefined) {
      await connection.stop();
    }
    await backend.close();
    removeDir(dir);
  });

  it("sends hello with the bearer secret and reports connected after hello_ack", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    const first = backend.connections[0];
    expect(first?.authorization).toBe(`Bearer ${TEST_SECRET}`);
    expect(first?.path).toBe(WS_PATH);
    const hello = backend.framesOf("hello", first)[0];
    expect(hello).toMatchObject({ type: "hello", agent_version: "1.0.0", os: "simulated" });
    expect(hello).toHaveProperty("resources.apps");
    expect(connection?.isConnected).toBe(true);
  });

  it("hands a signed command to the agent as raw text", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    const text = signedCommandText({ args: { app_id: "chrome" } }, Date.now());
    backend.connections[0]?.socket.send(text);
    await waitFor(() => commands.length === 1);
    expect(commands[0]).toBe(text);
  });

  it("hands a cancel frame to the agent as raw text", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    const text = signedCancelText("cmd-to-stop", Date.now());
    backend.connections[0]?.socket.send(text);
    await waitFor(() => cancels.length === 1);
    expect(cancels[0]).toBe(text);
  });

  it("sends a result that the backend receives as a result frame", async () => {
    const agent = make();
    agent.start();
    await waitFor(() => kinds().includes("connected"));
    await agent.sendResult(resultFor("cmd-1"));
    await waitFor(() => backend.framesOf("result").length === 1);
    expect(backend.framesOf("result")[0]).toMatchObject({ command_id: "cmd-1", status: "succeeded" });
  });

  it("removes a result from the outbox once a later heartbeat has gone out without error", async () => {
    let running: string[] = [];
    const agent = make({ runningCommandIds: () => running });
    agent.start();
    await waitFor(() => kinds().includes("connected"));
    running = ["cmd-busy"];
    await agent.sendResult(resultFor("cmd-2"));
    expect(outbox.has("cmd-2")).toBe(true);
    await waitFor(() => !outbox.has("cmd-2"), 4000);
    // The agent drops the entry as soon as the socket accepts the heartbeat. The fake backend reads that frame on a
    // later tick, so wait for it to arrive before inspecting what the backend received.
    await waitFor(
      () => backend.framesOf("heartbeat").some((frame) => JSON.stringify(frame.running_command_ids) === '["cmd-busy"]'),
      4000,
    );
    const heartbeats = backend.framesOf("heartbeat");
    expect(heartbeats.length).toBeGreaterThan(0);
    expect(heartbeats.some((frame) => JSON.stringify(frame.running_command_ids) === '["cmd-busy"]')).toBe(true);
  });

  it("answers ping with pong carrying the same nonce", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    backend.connections[0]?.socket.send(JSON.stringify({ type: "ping", nonce: "nonce-abc", sent_at: new Date().toISOString() }));
    await waitFor(() => backend.framesOf("pong").length === 1);
    expect(backend.framesOf("pong")[0]).toEqual({ type: "pong", nonce: "nonce-abc" });
  });

  it("ignores unknown and malformed frames and stays connected", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    const socket = backend.connections[0]?.socket;
    socket?.send(JSON.stringify({ type: "mystery_frame", data: 1 }));
    socket?.send("this is not json");
    socket?.send(JSON.stringify({ type: "command", id: "missing-fields" }));
    backend.connections[0]?.socket.send(JSON.stringify({ type: "ping", nonce: "after-noise" }));
    await waitFor(() => backend.framesOf("pong").length === 1);
    expect(commands).toHaveLength(0);
    expect(kinds().filter((kind) => kind === "offline")).toHaveLength(0);
  });

  it("close code 4401 stops, reports re-pair-required and does not reconnect", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    backend.closeWith(requireConnection(backend, 0), 4401, "unauthorised");
    await waitFor(() => kinds().includes("re-pair-required"));
    await sleep(200);
    expect(backend.connections).toHaveLength(1);
    expect(connection?.isRunning).toBe(false);
  });

  it("close code 4403 reports revoked and does not reconnect", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    backend.closeWith(requireConnection(backend, 0), 4403, "revoked");
    await waitFor(() => kinds().includes("revoked"));
    await sleep(200);
    expect(backend.connections).toHaveLength(1);
  });

  it("close code 4409 reports replaced and does not reconnect", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    backend.closeWith(requireConnection(backend, 0), 4409, "superseded");
    await waitFor(() => kinds().includes("replaced"));
    await sleep(200);
    expect(backend.connections).toHaveLength(1);
  });

  it("a normal close reconnects with backoff and reports connected again", async () => {
    make().start();
    await waitFor(() => kinds().includes("connected"));
    backend.closeWith(requireConnection(backend, 0), 1000, "bye");
    await waitFor(() => backend.connections.length === 2 && kinds().filter((kind) => kind === "connected").length === 2);
    const offline = events.find((event) => event.kind === "offline");
    expect(offline).toMatchObject({ kind: "offline", closeCode: 1000 });
    expect(backend.framesOf("hello", backend.connections[1])).toHaveLength(1);
  });

  it("keeps a result written while offline and sends it after the next hello_ack", async () => {
    const agent = make();
    await agent.sendResult(resultFor("cmd-offline"));
    expect(outbox.has("cmd-offline")).toBe(true);
    expect(backend.connections).toHaveLength(0);

    agent.start();
    await waitFor(() => backend.framesOf("result").some((frame) => frame.command_id === "cmd-offline"));
    await waitFor(() => !outbox.has("cmd-offline"));
  });

  it("stop() closes the socket and does not reconnect", async () => {
    const agent = make();
    agent.start();
    await waitFor(() => kinds().includes("connected"));
    await agent.stop();
    await sleep(150);
    expect(backend.connections).toHaveLength(1);
    expect(agent.isRunning).toBe(false);
  });

  it("does not report connected when the server never acknowledges the hello", async () => {
    const silent = await startFakeBackend({ autoAck: false });
    try {
      const agent = make({ serverUrl: silent.url });
      agent.start();
      await waitFor(() => silent.connections.length === 1);
      await sleep(100);
      expect(kinds()).not.toContain("connected");
      await agent.stop();
    } finally {
      await silent.close();
    }
  });

  it("refuses plain ws for a remote host at construction time", () => {
    expect(
      () =>
        new AgentConnection({
          serverUrl: "http://jarvis.example.com",
          websocketPath: WS_PATH,
          secret: TEST_SECRET,
          buildHello,
          outbox,
          runningCommandIds: () => [],
          onCommandFrame: () => undefined,
          onCancelFrame: () => undefined,
          onEvent: () => undefined,
        }),
    ).toThrow(ServerUrlError);
  });
});
