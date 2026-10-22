/**
 * Approved project checks (run_check): config validation, verification, the action through the dispatcher on the
 * simulated platform, the output cap, and the Windows capture path with a fake child process (no real process starts).
 */
import { EventEmitter } from "node:events";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { Dispatcher } from "../src/agent/dispatcher.js";
import { ReplayCache, verifyCommand, type VerifiedCommand } from "../src/agent/command-verifier.js";
import {
  ConfigError,
  enabledCapabilities,
  isCapabilityEnabled,
  loadConfig,
  parseConfigDetailed,
  resourcesForServer,
  type AgentConfig,
  type ApprovedCheck,
} from "../src/agent/config.js";
import type { Logger } from "../src/agent/logger.js";
import { DEFAULT_HANDLERS } from "../src/agent/actions/index.js";
import { SimulatedPlatform } from "../src/platform/simulated.js";
import { PlatformError, type CaptureRequest } from "../src/platform/platform.js";
import { CHECK_ENVIRONMENT_NAMES, WindowsPlatform, checkEnvironment, type SpawnFunction } from "../src/platform/windows.js";
import { OutputTail, tailOfText } from "../src/platform/output-tail.js";
import type { AgentResult } from "../src/shared/protocol.js";
import { fixtureConfig, removeDir, signedCommandText, TEST_SECRET, tempDir, waitFor } from "./helpers.js";

const NOW = Date.parse("2026-10-21T09:00:00Z");

const CHECK: ApprovedCheck = {
  id: "tests",
  label: "Unit tests",
  command: "C:\\Tools\\node\\node.exe",
  args: ["--test"],
  cwd: "C:\\repo",
  timeoutSeconds: 120,
};

function configWithChecks(checks: ApprovedCheck[]): AgentConfig {
  return { ...fixtureConfig(), approvedChecks: checks };
}

function configText(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: 1,
    approvedApps: [],
    approvedUrls: [],
    approvedFolders: [],
    approvedProjects: [],
    approvedFiles: [],
    capabilities: {},
    pairing: null,
    ...extra,
  });
}

const GOOD_RAW = { id: "tests", label: "Unit tests", command: "C:\\Tools\\node\\node.exe", args: ["--test"], cwd: "C:\\repo", timeoutSeconds: 120 };

function recordingLogger(): { logger: Logger; warnings: Array<{ message: string; fields: Record<string, unknown> | undefined }> } {
  const warnings: Array<{ message: string; fields: Record<string, unknown> | undefined }> = [];
  const logger: Logger = {
    debug: () => undefined,
    info: () => undefined,
    warn: (message, fields) => {
      warnings.push({ message, fields });
    },
    error: () => undefined,
  };
  return { logger, warnings };
}

function verifiedRunCheck(config: AgentConfig, args: unknown): ReturnType<typeof verifyCommand> {
  return verifyCommand(signedCommandText({ action: "run_check", args }, NOW), {
    secret: TEST_SECRET,
    config,
    nowMs: NOW,
    replay: new ReplayCache(),
  });
}

function commandFor(config: AgentConfig, checkId: string): VerifiedCommand {
  const result = verifiedRunCheck(config, { check_id: checkId });
  if (!result.ok) {
    throw new Error(`fixture rejected: ${result.code}`);
  }
  return result.command;
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

describe("config: approved checks are validated, and invalid ones are skipped", () => {
  it("a valid check gets the defaults: no args means an empty list, no timeout means 300 seconds", () => {
    const raw = { id: "lint", label: "Lint", command: "C:\\Tools\\eslint.exe", cwd: "C:\\repo" };
    const { config, skippedChecks } = parseConfigDetailed(configText({ approvedChecks: [raw] }));
    expect(skippedChecks).toEqual([]);
    expect(config.approvedChecks).toEqual([{ ...raw, args: [], timeoutSeconds: 300 }]);
  });

  it.each([
    ["a relative command", { ...GOOD_RAW, id: "bad", command: "node.exe" }],
    ["a relative cwd", { ...GOOD_RAW, id: "bad", cwd: "repo" }],
    ["an unknown field", { ...GOOD_RAW, id: "bad", shell: "cmd /c whoami" }],
    ["a timeout below 10 seconds", { ...GOOD_RAW, id: "bad", timeoutSeconds: 9 }],
    ["a timeout above 600 seconds", { ...GOOD_RAW, id: "bad", timeoutSeconds: 601 }],
    ["a fractional timeout", { ...GOOD_RAW, id: "bad", timeoutSeconds: 12.5 }],
    ["more than 32 arguments", { ...GOOD_RAW, id: "bad", args: Array.from({ length: 33 }, () => "x") }],
    ["an argument over 200 characters", { ...GOOD_RAW, id: "bad", args: ["x".repeat(201)] }],
    ["an argument with a NUL byte", { ...GOOD_RAW, id: "bad", args: ["a\u0000b"] }],
    ["a bad id", { ...GOOD_RAW, id: "Bad Id" }],
    ["a blank label", { ...GOOD_RAW, id: "bad", label: "   " }],
  ])("skips a check with %s and keeps the valid one", (_name, bad) => {
    const { config, skippedChecks } = parseConfigDetailed(configText({ approvedChecks: [bad, GOOD_RAW] }));
    expect(skippedChecks).toHaveLength(1);
    expect(skippedChecks[0]?.index).toBe(0);
    expect(config.approvedChecks.map((check) => check.id)).toEqual(["tests"]);
  });

  it("skips a later entry that reuses an id, so each id names one command", () => {
    const second = { ...GOOD_RAW, command: "C:\\Other\\other.exe" };
    const { config, skippedChecks } = parseConfigDetailed(configText({ approvedChecks: [GOOD_RAW, second] }));
    expect(skippedChecks).toEqual([{ index: 1, reason: "duplicate id" }]);
    expect(config.approvedChecks[0]?.command).toBe("C:\\Tools\\node\\node.exe");
  });

  it("skip reasons name fields only and never echo the command or arguments", () => {
    const secret = "C:\\secret-folder\\token-abc123.exe";
    const { skippedChecks } = parseConfigDetailed(configText({ approvedChecks: [{ ...GOOD_RAW, id: "x", cwd: secret.replace(".exe", "") }] }));
    expect(JSON.stringify(skippedChecks)).not.toContain("token-abc123");
  });

  it("a run_check flag in capabilities is refused: run_check is enabled by its checks, not by a flag", () => {
    expect(() => parseConfigDetailed(configText({ capabilities: { run_check: true } }))).toThrow(ConfigError);
  });

  it("an agent.json written before approved checks existed still loads with none", () => {
    const { config } = parseConfigDetailed(configText());
    expect(config.approvedChecks).toEqual([]);
  });

  it("loadConfig skips a check whose command or folder is missing, logs it, and keeps the one that exists", async () => {
    const dir = tempDir();
    try {
      const tool = path.join(dir, "tool.exe");
      fs.writeFileSync(tool, "");
      const good = { id: "present", label: "Present", command: tool, args: [], cwd: dir, timeoutSeconds: 60 };
      const missingCommand = { ...good, id: "gone", command: path.join(dir, "missing.exe") };
      const missingCwd = { ...good, id: "nofolder", cwd: path.join(dir, "no-such-folder") };
      const file = path.join(dir, "agent.json");
      fs.writeFileSync(file, configText({ approvedChecks: [good, missingCommand, missingCwd] }));
      const { logger, warnings } = recordingLogger();
      const config = await loadConfig(file, logger);
      expect(config.approvedChecks.map((check) => check.id)).toEqual(["present"]);
      expect(warnings.map((warning) => warning.fields?.reason)).toEqual(["command does not exist", "cwd does not exist"]);
      expect(JSON.stringify(warnings)).not.toContain(dir);
    } finally {
      removeDir(dir);
    }
  });

  it("run_check is reported only while a valid check exists, and resources carry id and label only", () => {
    const none = fixtureConfig();
    expect(enabledCapabilities(none)).not.toContain("run_check");
    expect(isCapabilityEnabled(none, "run_check")).toBe(false);

    const withCheck = configWithChecks([CHECK]);
    expect(enabledCapabilities(withCheck)).toContain("run_check");
    expect(isCapabilityEnabled(withCheck, "run_check")).toBe(true);

    const resources = resourcesForServer(withCheck);
    expect(resources.checks).toEqual([{ id: "tests", label: "Unit tests" }]);
    const wire = JSON.stringify(resources);
    expect(wire).not.toContain("node.exe");
    expect(wire).not.toContain("--test");
    expect(wire).not.toContain("C:\\\\repo");
  });
});

describe("verifying run_check commands", () => {
  it("accepts check_id and resolves the local check, matched by id or label", () => {
    const config = configWithChecks([CHECK]);
    const byId = verifiedRunCheck(config, { check_id: "tests" });
    expect(byId.ok).toBe(true);
    if (byId.ok) {
      expect(byId.command.action).toBe("run_check");
      expect(byId.command.target).toMatchObject({ action: "run_check", check: { id: "tests", command: CHECK.command } });
    }
  });

  it("refuses any key other than check_id", () => {
    const result = verifiedRunCheck(configWithChecks([CHECK]), { check_id: "tests", command: "C:\\evil.exe" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("invalid_arguments");
    }
  });

  it("refuses a check id that is not approved with resource_not_approved", () => {
    const result = verifiedRunCheck(configWithChecks([CHECK]), { check_id: "deploy" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("resource_not_approved");
    }
  });

  it("refuses run_check with permission_denied when no check is configured", () => {
    const result = verifiedRunCheck(fixtureConfig(), { check_id: "tests" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("permission_denied");
    }
  });
});

describe("run_check through the dispatcher on the simulated platform", () => {
  it("exit 0 succeeds with the captured output and a SIMULATED summary", async () => {
    const platform = new SimulatedPlatform({ captureScript: { kind: "exit", exitCode: 0, output: "all tests passed\n" } });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(commandFor(configWithChecks([CHECK]), "tests"));
    await dispatcher.idle();
    expect(results).toHaveLength(1);
    const [result] = results;
    expect(result?.status).toBe("succeeded");
    expect(result?.output).toMatchObject({
      check_id: "tests",
      label: "Unit tests",
      exit_code: 0,
      output_tail: "all tests passed\n",
      truncated: false,
    });
    expect(typeof result?.output.duration_ms).toBe("number");
    expect(String(result?.output.summary)).toMatch(/^SIMULATED: /);
  });

  it("a non-zero exit is failed with check_failed and the same output", async () => {
    const platform = new SimulatedPlatform({ captureScript: { kind: "exit", exitCode: 3, output: "2 failing" } });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(commandFor(configWithChecks([CHECK]), "tests"));
    await dispatcher.idle();
    const [result] = results;
    expect(result?.status).toBe("failed");
    expect(result?.error?.code).toBe("check_failed");
    expect(result?.output).toMatchObject({ check_id: "tests", exit_code: 3, output_tail: "2 failing" });
  });

  it("a run that never ends in time is failed with timeout and the process is reported stopped", async () => {
    const platform = new SimulatedPlatform({ captureScript: { kind: "hang", output: "still working" } });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    // The config would not allow a timeout this short, so the command is built directly for this test.
    const shortCheck: ApprovedCheck = { ...CHECK, timeoutSeconds: 0.05 };
    const command: VerifiedCommand = {
      id: "00000000-0000-4000-8000-00000000aaaa",
      action: "run_check",
      target: { action: "run_check", check: shortCheck },
      issuedAt: NOW,
      expiresAt: NOW + 60_000,
    };
    dispatcher.submit(command);
    await dispatcher.idle();
    const [result] = results;
    expect(result?.status).toBe("failed");
    expect(result?.error?.code).toBe("timeout");
    expect(result?.output).toMatchObject({ check_id: "tests", exit_code: null, output_tail: "still working" });
  });

  it("cancelling a running check kills it and reports cancelled_by_user", async () => {
    const platform = new SimulatedPlatform({ captureScript: { kind: "hang", output: "" } });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    const command = commandFor(configWithChecks([CHECK]), "tests");
    dispatcher.submit(command);
    await waitFor(() => platform.calls.some((call) => call.method === "runCapture"));
    expect(dispatcher.cancel(command.id)).toEqual({ cancelled: true });
    await dispatcher.idle();
    const [result] = results;
    expect(result?.status).toBe("cancelled");
    expect(result?.error?.code).toBe("cancelled_by_user");
  });

  it("a check that cannot be started is reported with the platform's code", async () => {
    const platform = new SimulatedPlatform();
    platform.runCapture = async () => {
      throw new PlatformError("not_installed", "The check command was not found as a file.");
    };
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult, handlers: DEFAULT_HANDLERS });
    dispatcher.submit(commandFor(configWithChecks([CHECK]), "tests"));
    await dispatcher.idle();
    expect(results[0]?.status).toBe("failed");
    expect(results[0]?.error?.code).toBe("not_installed");
  });

  it("the output sent back is capped at 4 KB and keeps the end of the output", async () => {
    const output = `${"a".repeat(10_000)}END-MARKER`;
    const platform = new SimulatedPlatform({ captureScript: { kind: "exit", exitCode: 0, output } });
    const { results, onResult } = collector();
    const dispatcher = new Dispatcher({ platform, onResult });
    dispatcher.submit(commandFor(configWithChecks([CHECK]), "tests"));
    await dispatcher.idle();
    const tail = String(results[0]?.output.output_tail);
    expect(Buffer.byteLength(tail, "utf8")).toBeLessThanOrEqual(4096);
    expect(tail.endsWith("END-MARKER")).toBe(true);
    expect(results[0]?.output.truncated).toBe(true);
  });
});

describe("output capture", () => {
  it("OutputTail keeps only the last bytes and marks the cut", () => {
    const tail = new OutputTail(8);
    tail.push(Buffer.from("0123456"));
    expect(tail.truncated).toBe(false);
    tail.push(Buffer.from("789ABC"));
    expect(tail.truncated).toBe(true);
    expect(tail.text()).toBe("56789ABC".slice(-8));
  });

  it("a cut never leaves half a character at the start", () => {
    const { output, truncated } = tailOfText("€".repeat(2000), 4096);
    expect(truncated).toBe(true);
    expect(output).not.toContain("\uFFFD");
    expect(Buffer.byteLength(output, "utf8")).toBeLessThanOrEqual(4096);
  });
});

/** A child_process stand-in: the test decides when output, exit or spawn errors happen. */
class FakeChild extends EventEmitter {
  pid: number | undefined = 4312;
  readonly stdout = new EventEmitter();
  readonly stderr = new EventEmitter();
  killed = false;
  kill(): boolean {
    this.killed = true;
    // A killed process closes its streams straight away in this fake.
    queueMicrotask(() => this.emit("close", null));
    return true;
  }
}

interface SpawnCall {
  executable: string;
  args: readonly string[];
  options: Record<string, unknown>;
}

function fakeSpawner(configure: (child: FakeChild, call: SpawnCall) => void): { spawnImpl: SpawnFunction; calls: SpawnCall[] } {
  const calls: SpawnCall[] = [];
  const spawnImpl: SpawnFunction = (executable, args, options) => {
    const call: SpawnCall = { executable, args, options: options as unknown as Record<string, unknown> };
    calls.push(call);
    const child = new FakeChild();
    queueMicrotask(() => configure(child, call));
    return child as unknown as ReturnType<SpawnFunction>;
  };
  return { spawnImpl, calls };
}

const WIN_ENV = {
  PATH: "C:\\Windows\\System32;C:\\Tools",
  SystemRoot: "C:\\Windows",
  ProgramFiles: "C:\\Program Files",
  USERPROFILE: "C:\\Users\\alex",
  APPDATA: "C:\\Users\\alex\\AppData\\Roaming",
  JARVIS_SECRET: "must-not-reach-the-check",
};

describe("WindowsPlatform.runCapture (fake child process, real files for the path checks)", () => {
  it("starts the command with an argv array, no shell, a hidden window and only the minimal environment", async () => {
    const dir = tempDir();
    try {
      const command = path.join(dir, "node.exe");
      fs.writeFileSync(command, "");
      const spawner = fakeSpawner((child) => {
        child.stdout.emit("data", Buffer.from("ok"));
        child.emit("close", 0);
      });
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      const outcome = await platform.runCapture({
        command,
        args: ["--test", "tests/a b.js"],
        cwd: dir,
        timeoutMs: 5_000,
        signal: new AbortController().signal,
        maxOutputBytes: 4096,
      });
      expect(outcome).toMatchObject({ kind: "exited", exitCode: 0, output: "ok", truncated: false });
      const [call] = spawner.calls;
      expect(call?.executable).toBe(command);
      expect(call?.args).toEqual(["--test", "tests/a b.js"]);
      expect(call?.options).toMatchObject({ shell: false, windowsHide: true, detached: false, cwd: dir });
      const env = call?.options.env as Record<string, string>;
      expect(Object.keys(env).sort()).toEqual(["PATH", "SystemRoot"]);
      expect(env.PATH).toBe(WIN_ENV.PATH);
      expect(env.SystemRoot).toBe("C:\\Windows");
      expect(checkEnvironment(WIN_ENV)).toEqual(env);
      expect([...CHECK_ENVIRONMENT_NAMES]).toEqual(["PATH", "SystemRoot"]);
    } finally {
      removeDir(dir);
    }
  });

  it("a non-zero exit is reported with its code and the captured output", async () => {
    const dir = tempDir();
    try {
      const command = path.join(dir, "tool.exe");
      fs.writeFileSync(command, "");
      const spawner = fakeSpawner((child) => {
        child.stderr.emit("data", Buffer.from("boom"));
        child.emit("close", 2);
      });
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      const outcome = await platform.runCapture({
        command,
        args: [],
        cwd: dir,
        timeoutMs: 5_000,
        signal: new AbortController().signal,
        maxOutputBytes: 4096,
      });
      expect(outcome).toMatchObject({ kind: "exited", exitCode: 2, output: "boom" });
    } finally {
      removeDir(dir);
    }
  });

  it("a timeout kills the process tree with taskkill /T /F /PID and reports timed_out", async () => {
    const dir = tempDir();
    try {
      const command = path.join(dir, "tool.exe");
      fs.writeFileSync(command, "");
      const spawner = fakeSpawner((child, call) => {
        if (call.executable === command) {
          child.stdout.emit("data", Buffer.from("partial"));
        }
      });
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      const outcome = await platform.runCapture({
        command,
        args: [],
        cwd: dir,
        timeoutMs: 30,
        signal: new AbortController().signal,
        maxOutputBytes: 4096,
      });
      expect(outcome).toMatchObject({ kind: "timed_out", output: "partial" });
      const killer = spawner.calls.find((call) => call.executable !== command);
      expect(killer?.executable.endsWith(path.win32.join("System32", "taskkill.exe"))).toBe(true);
      expect(killer?.args).toEqual(["/T", "/F", "/PID", "4312"]);
      expect(killer?.options).toMatchObject({ shell: false, windowsHide: true });
    } finally {
      removeDir(dir);
    }
  });

  it("an abort kills the tree and reports cancelled", async () => {
    const dir = tempDir();
    try {
      const command = path.join(dir, "tool.exe");
      fs.writeFileSync(command, "");
      const controller = new AbortController();
      const spawner = fakeSpawner((_child, call) => {
        if (call.executable === command) {
          queueMicrotask(() => controller.abort());
        }
      });
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      const outcome = await platform.runCapture({
        command,
        args: [],
        cwd: dir,
        timeoutMs: 60_000,
        signal: controller.signal,
        maxOutputBytes: 4096,
      });
      expect(outcome.kind).toBe("cancelled");
      expect(spawner.calls.some((call) => call.args[0] === "/T" && call.args[1] === "/F")).toBe(true);
    } finally {
      removeDir(dir);
    }
  });

  it("refuses a relative command, a missing file and a missing folder before starting anything", async () => {
    const dir = tempDir();
    try {
      const spawner = fakeSpawner(() => undefined);
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      const base = { args: [], timeoutMs: 1000, signal: new AbortController().signal, maxOutputBytes: 4096 };
      await expect(platform.runCapture({ ...base, command: "node.exe", cwd: dir })).rejects.toMatchObject({ code: "execution_failed" });
      await expect(platform.runCapture({ ...base, command: path.join(dir, "missing.exe"), cwd: dir })).rejects.toMatchObject({
        code: "not_installed",
      });
      fs.writeFileSync(path.join(dir, "tool.exe"), "");
      await expect(
        platform.runCapture({ ...base, command: path.join(dir, "tool.exe"), cwd: path.join(dir, "nope") }),
      ).rejects.toMatchObject({ code: "execution_failed" });
      expect(spawner.calls).toHaveLength(0);
    } finally {
      removeDir(dir);
    }
  });

  it("a process that fails to start maps ENOENT to not_installed", async () => {
    const dir = tempDir();
    try {
      const command = path.join(dir, "tool.exe");
      fs.writeFileSync(command, "");
      const spawner = fakeSpawner((child) => {
        child.pid = undefined;
        const error = Object.assign(new Error("spawn failed"), { code: "ENOENT" });
        child.emit("error", error);
      });
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      await expect(
        platform.runCapture({ command, args: [], cwd: dir, timeoutMs: 1000, signal: new AbortController().signal, maxOutputBytes: 4096 }),
      ).rejects.toMatchObject({ code: "not_installed" });
    } finally {
      removeDir(dir);
    }
  });

  it("the captured output is capped to the request's byte limit", async () => {
    const dir = tempDir();
    try {
      const command = path.join(dir, "tool.exe");
      fs.writeFileSync(command, "");
      const spawner = fakeSpawner((child) => {
        child.stdout.emit("data", Buffer.from("x".repeat(9000)));
        child.emit("close", 0);
      });
      const platform = new WindowsPlatform({ env: WIN_ENV, spawnImpl: spawner.spawnImpl });
      const request: CaptureRequest = {
        command,
        args: [],
        cwd: dir,
        timeoutMs: 5000,
        signal: new AbortController().signal,
        maxOutputBytes: 4096,
      };
      const outcome = await platform.runCapture(request);
      expect(outcome.kind).toBe("exited");
      expect(Buffer.byteLength(outcome.output, "utf8")).toBe(4096);
      expect(outcome.truncated).toBe(true);
    } finally {
      removeDir(dir);
    }
  });
});
