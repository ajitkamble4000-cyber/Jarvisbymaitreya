import { describe, expect, it } from "vitest";
import { Dispatcher, CANCELLED_MESSAGE } from "../src/agent/dispatcher.js";
import { ReplayCache, verifyCommand, type VerifiedCommand } from "../src/agent/command-verifier.js";
import { DEFAULT_HANDLERS, type ActionHandlers } from "../src/agent/actions/index.js";
import { SimulatedPlatform, SIMULATED_BANNER } from "../src/platform/simulated.js";
import type { SpawnRequest } from "../src/platform/platform.js";
import type { AgentResult } from "../src/shared/protocol.js";
import { fixtureConfig, signedCommandText, TEST_SECRET } from "./helpers.js";

const NOW = Date.parse("2026-10-19T04:51:40Z");

function verified(args: unknown, action = "launch_app", id?: string): VerifiedCommand {
  const result = verifyCommand(
    signedCommandText({ action, args, id, nonce: `n-${id ?? Math.random()}` }, NOW),
    { secret: TEST_SECRET, config: fixtureConfig(), nowMs: NOW, replay: new ReplayCache() },
  );
  if (!result.ok) {
    throw new Error(`fixture rejected: ${result.code}`);
  }
  return result.command;
}

function deferred<T = void>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function collector(): { results: AgentResult[]; onResult: (result: AgentResult) => Promise<void> } {
  const results: AgentResult[] = [];
  return {
    results,
    onResult: async (result) => {
      results.push(result);
    },
  };
}

describe("simulated platform actions through the dispatcher", () => {
  it("launch_app succeeds with a simulated pid and says it is simulated", async () => {
    const platform = new SimulatedPlatform({ firstPid: 4312 });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "chrome" }));
    await dispatcher.idle();
    expect(results).toHaveLength(1);
    const [result] = results;
    expect(result?.status).toBe("succeeded");
    expect(result?.output).toMatchObject({ pid: 4312, app_id: "chrome", verified: false });
    expect(String(result?.output.summary)).toMatch(/^SIMULATED: Started Google Chrome \(pid 4312\)\./);
    expect(platform.calls.map((call) => call.method)).toContain("spawnDetached");
  });

  it("launch_app reports not_installed when the app is not found", async () => {
    const platform = new SimulatedPlatform({ installedAppIds: [] });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "vscode" }));
    await dispatcher.idle();
    expect(results[0]).toMatchObject({ status: "failed", error: { code: "not_installed" } });
  });

  it("open_url asks the platform to open the approved URL and reports verified false", async () => {
    const platform = new SimulatedPlatform();
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ url_id: "youtube" }, "open_url"));
    await dispatcher.idle();
    expect(results[0]?.status).toBe("succeeded");
    expect(results[0]?.output).toMatchObject({ url: "https://www.youtube.com", verified: false });
    expect(platform.calls).toContainEqual({ method: "openExternal", detail: { scheme: "https:" } });
  });

  it("open_url reports execution_failed when the host refuses", async () => {
    const platform = new SimulatedPlatform({ openExternalFails: true });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ url_id: "youtube" }, "open_url"));
    await dispatcher.idle();
    expect(results[0]).toMatchObject({ status: "failed", error: { code: "execution_failed" } });
  });

  it("open_folder fails when the approved folder is missing on this device", async () => {
    const platform = new SimulatedPlatform({ existingFolders: [] });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ folder_id: "downloads" }, "open_folder"));
    await dispatcher.idle();
    expect(results[0]).toMatchObject({ status: "failed", error: { code: "execution_failed" } });
  });

  it("open_folder succeeds for an existing folder without reporting a pid", async () => {
    const platform = new SimulatedPlatform();
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ folder_id: "downloads" }, "open_folder"));
    await dispatcher.idle();
    expect(results[0]?.status).toBe("succeeded");
    expect(Object.keys(results[0]?.output ?? {}).sort()).toEqual(["folder_id", "summary", "verified"]);
  });

  it("open_project returns exactly the documented output fields", async () => {
    const platform = new SimulatedPlatform({ firstPid: 5001 });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ project_id: "jarvis" }, "open_project"));
    await dispatcher.idle();
    expect(results[0]?.status).toBe("succeeded");
    expect(Object.keys(results[0]?.output ?? {}).sort()).toEqual(["app_id", "pid", "project_id", "summary"]);
    expect(results[0]?.output).toMatchObject({ project_id: "jarvis", app_id: "vscode", pid: 5001 });
  });

  it("system_info returns exactly the documented fields", async () => {
    const platform = new SimulatedPlatform();
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({}, "system_info"));
    await dispatcher.idle();
    expect(Object.keys(results[0]?.output ?? {}).sort()).toEqual(
      ["arch", "cpu_count", "free_memory_mb", "hostname", "os", "os_version", "total_memory_mb", "uptime_seconds"].sort(),
    );
    expect(results[0]?.output.os).toBe("simulated");
  });

  it("read_file returns content and truncates at 64 KB", async () => {
    const big = "x".repeat(70 * 1024);
    const platform = new SimulatedPlatform({ files: { [fixtureConfig().approvedFiles[0]?.path ?? ""]: big } });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ file_id: "notes" }, "read_file"));
    await dispatcher.idle();
    expect(results[0]?.status).toBe("succeeded");
    expect(results[0]?.output).toMatchObject({ file_id: "notes", truncated: true, size_bytes: 70 * 1024 });
    expect(Buffer.byteLength(String(results[0]?.output.content), "utf8")).toBe(64 * 1024);
  });

  it("read_file fails without revealing the local path", async () => {
    const platform = new SimulatedPlatform({ files: {} });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ file_id: "notes" }, "read_file"));
    await dispatcher.idle();
    expect(results[0]).toMatchObject({ status: "failed", error: { code: "execution_failed" } });
    expect(JSON.stringify(results[0])).not.toContain("/fixture");
  });

  it("app_availability answers installed true or false as a success", async () => {
    const present = collector();
    const dispatcherA = new Dispatcher({ platform: new SimulatedPlatform(), onResult: present.onResult });
    dispatcherA.submit(verified({ app_id: "chrome" }, "app_availability"));
    await dispatcherA.idle();
    expect(present.results[0]).toMatchObject({ status: "succeeded", output: { app_id: "chrome", installed: true } });

    const absent = collector();
    const dispatcherB = new Dispatcher({ platform: new SimulatedPlatform({ installedAppIds: [] }), onResult: absent.onResult });
    dispatcherB.submit(verified({ app_id: "chrome" }, "app_availability"));
    await dispatcherB.idle();
    expect(absent.results[0]).toMatchObject({ status: "succeeded", output: { installed: false } });
  });

  it("the simulated platform banner is the documented text", () => {
    expect(new SimulatedPlatform().banner).toBe(SIMULATED_BANNER);
    expect(SIMULATED_BANNER).toBe("SIMULATED PLATFORM - no real actions performed");
  });
});

/** Platform wrapper that records start and end of each spawn, and lets a test hold a spawn open. */
class GatedPlatform extends SimulatedPlatform {
  readonly events: string[] = [];
  inFlight = 0;
  maxInFlight = 0;
  holdSpawn: Promise<void> | null = null;
  readonly spawnStarted = deferred();
  findGate: Promise<void> | null = null;

  override async findExecutable(lookup: { id: string; candidates: readonly string[] }): Promise<string | null> {
    if (this.findGate !== null) {
      await this.findGate;
    }
    return super.findExecutable(lookup);
  }

  override async spawnDetached(request: SpawnRequest): Promise<{ pid: number }> {
    this.inFlight += 1;
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
    this.events.push(`start:${request.args.length}`);
    try {
      const result = await super.spawnDetached({
        ...request,
        onStarted: (pid) => {
          request.onStarted(pid);
          this.spawnStarted.resolve();
        },
      });
      if (this.holdSpawn !== null) {
        await this.holdSpawn;
      }
      this.events.push(`end:${result.pid}`);
      return result;
    } finally {
      this.inFlight -= 1;
    }
  }
}

describe("Dispatcher ordering and cancellation", () => {
  it("runs one command at a time per action class", async () => {
    const platform = new GatedPlatform({ settleMs: 15, firstPid: 100 });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "chrome" }, "launch_app", "order-a"));
    dispatcher.submit(verified({ app_id: "vscode" }, "launch_app", "order-b"));
    await dispatcher.idle();
    expect(platform.maxInFlight).toBe(1);
    expect(results.map((result) => result.command_id)).toEqual(["order-a", "order-b"]);
  });

  it("lets different action classes run at the same time", async () => {
    const platform = new GatedPlatform({ settleMs: 80, firstPid: 200 });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "chrome" }, "launch_app", "slow-launch"));
    dispatcher.submit(verified({}, "system_info", "quick-info"));
    await dispatcher.idle();
    expect(results.map((result) => result.command_id)).toEqual(["quick-info", "slow-launch"]);
  });

  it("cancels a queued command before it starts and never spawns it", async () => {
    const platform = new GatedPlatform({ settleMs: 40, firstPid: 300 });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "chrome" }, "launch_app", "first"));
    dispatcher.submit(verified({ app_id: "vscode" }, "launch_app", "second"));
    expect(dispatcher.cancel("second")).toEqual({ cancelled: true });
    await dispatcher.idle();
    expect(platform.events.filter((event) => event.startsWith("start"))).toHaveLength(1);
    const second = results.find((result) => result.command_id === "second");
    expect(second).toMatchObject({ status: "cancelled", error: { code: "cancelled_by_user", message: CANCELLED_MESSAGE } });
    expect(second?.started_at).toBeNull();
  });

  it("cancels a running action that has not produced a pid yet", async () => {
    const platform = new GatedPlatform({ firstPid: 400 });
    const gate = deferred();
    platform.findGate = gate.promise;
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "chrome" }, "launch_app", "midway"));
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(dispatcher.runningCommandIds()).toEqual(["midway"]);
    expect(dispatcher.cancel("midway")).toEqual({ cancelled: true });
    gate.resolve();
    await dispatcher.idle();
    expect(results[0]).toMatchObject({ status: "cancelled", error: { code: "cancelled_by_user" } });
    expect(platform.events).toEqual([]);
  });

  it("refuses to cancel once the process has started, and says so in the result", async () => {
    const platform = new GatedPlatform({ firstPid: 500 });
    const hold = deferred();
    platform.holdSpawn = hold.promise;
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(verified({ app_id: "chrome" }, "launch_app", "started"));
    await platform.spawnStarted.promise;
    expect(dispatcher.cancel("started")).toEqual({ cancelled: false, reason: "already_started" });
    hold.resolve();
    await dispatcher.idle();
    expect(results[0]?.status).toBe("succeeded");
    expect(String(results[0]?.output.summary)).toMatch(/cannot be undone/);
  });

  it("reports unknown or finished commands as not cancelled", async () => {
    const { onResult } = collector();
    const dispatcher = new Dispatcher({ platform: new SimulatedPlatform(), onResult });
    expect(dispatcher.cancel("never-seen")).toEqual({ cancelled: false, reason: "unknown_or_finished" });
    dispatcher.submit(verified({}, "system_info", "done-soon"));
    await dispatcher.idle();
    expect(dispatcher.cancel("done-soon")).toEqual({ cancelled: false, reason: "unknown_or_finished" });
  });

  it("ignores a second submit of an id that is already queued or running", async () => {
    const platform = new GatedPlatform({ settleMs: 20, firstPid: 600 });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    const command = verified({ app_id: "chrome" }, "launch_app", "dup");
    expect(dispatcher.submit(command)).toBe(true);
    expect(dispatcher.submit(command)).toBe(false);
    await dispatcher.idle();
    expect(results).toHaveLength(1);
  });

  it("turns an unexpected handler error into agent_error without leaking its text", async () => {
    const handlers: ActionHandlers = {
      ...DEFAULT_HANDLERS,
      system_info: async () => {
        throw new Error("boom at C:\\Users\\alex\\secret.txt");
      },
    };
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform: new SimulatedPlatform(), onResult, handlers });
    dispatcher.submit(verified({}, "system_info", "explode"));
    await dispatcher.idle();
    expect(results[0]).toMatchObject({ status: "failed", error: { code: "agent_error" } });
    expect(JSON.stringify(results[0])).not.toContain("secret.txt");
  });
});
