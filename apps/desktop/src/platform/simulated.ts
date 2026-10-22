/**
 * SimulatedPlatform: for tests and the headless dev runner only. It records every call and returns configurable
 * results. It never starts a process, opens a URL or reads a file from disk. Every result it produces says so.
 */
import * as os from "node:os";
import {
  PlatformError,
  assertHttpUrl,
  type CaptureOutcome,
  type CaptureRequest,
  type ExecutableLookup,
  type Platform,
  type ReadFileResult,
  type SpawnRequest,
  type SystemInfo,
} from "./platform.js";
import { tailOfText } from "./output-tail.js";

export const SIMULATED_BANNER = "SIMULATED PLATFORM - no real actions performed";

/**
 * What a simulated check run does. "exit" ends with the given code and output. "hang" never exits on its own: it ends
 * as cancelled when the request's signal aborts, or as timed_out when the request's timeout passes. Tests use it to
 * drive every path without a real process.
 */
export type CaptureScript =
  | { kind: "exit"; exitCode: number; output: string }
  | { kind: "hang"; output: string };

export interface SimulatedCall {
  method: string;
  detail: Record<string, string | number | boolean>;
}

export interface SimulatedPlatformOptions {
  /** App ids reported as installed. Defaults to every app id that is looked up. */
  installedAppIds?: readonly string[] | "all";
  /** Folder paths reported as existing. Defaults to every path. */
  existingFolders?: readonly string[] | "all";
  /** File contents by path. A path missing here fails with execution_failed. */
  files?: Readonly<Record<string, string>>;
  /** Makes every spawn fail with the given error code instead of returning a pid. */
  spawnFailure?: "not_installed" | "execution_failed";
  openExternalFails?: boolean;
  /** Result of every runCapture call. Defaults to a successful exit with no output. */
  captureScript?: CaptureScript;
  firstPid?: number;
  settleMs?: number;
  systemInfo?: Partial<SystemInfo>;
  now?: () => Date;
}

export class SimulatedPlatform implements Platform {
  readonly kind = "simulated" as const;
  readonly simulated = true;
  readonly banner = SIMULATED_BANNER;
  readonly calls: SimulatedCall[] = [];

  private readonly options: SimulatedPlatformOptions;
  private nextPid: number;

  constructor(options: SimulatedPlatformOptions = {}) {
    this.options = options;
    this.nextPid = options.firstPid ?? 4000;
  }

  private record(method: string, detail: SimulatedCall["detail"] = {}): void {
    this.calls.push({ method, detail });
  }

  async findExecutable(lookup: ExecutableLookup): Promise<string | null> {
    this.record("findExecutable", { appId: lookup.id });
    const installed = this.options.installedAppIds ?? "all";
    const isInstalled = installed === "all" || installed.includes(lookup.id);
    return isInstalled ? `simulated://${lookup.id}` : null;
  }

  async spawnDetached(request: SpawnRequest): Promise<{ pid: number }> {
    this.record("spawnDetached", { executable: request.executable, argCount: request.args.length });
    if (request.signal.aborted) {
      throw new PlatformError("cancelled", "The start was cancelled before the process was created.");
    }
    if (this.options.spawnFailure === "not_installed") {
      throw new PlatformError("not_installed", "The application is not installed.");
    }
    if (this.options.spawnFailure === "execution_failed") {
      throw new PlatformError("execution_failed", "The application could not be started.");
    }
    const pid = this.nextPid;
    this.nextPid += 1;
    request.onStarted(pid);
    await delay(this.options.settleMs ?? 0);
    return { pid };
  }

  async openExternal(url: string): Promise<void> {
    assertHttpUrl(url);
    this.record("openExternal", { scheme: new URL(url).protocol });
    if (this.options.openExternalFails === true) {
      throw new PlatformError("execution_failed", "The default browser could not be asked to open the URL.");
    }
  }

  async openFolder(folderPath: string): Promise<void> {
    this.record("openFolder", { pathKnown: folderPath.length > 0 });
  }

  async folderExists(folderPath: string): Promise<boolean> {
    this.record("folderExists", { pathKnown: folderPath.length > 0 });
    const folders = this.options.existingFolders ?? "all";
    return folders === "all" || folders.includes(folderPath);
  }

  async readFileLimited(filePath: string, maxBytes: number): Promise<ReadFileResult> {
    this.record("readFileLimited", { maxBytes });
    const files = this.options.files ?? {};
    const content = Object.prototype.hasOwnProperty.call(files, filePath) ? files[filePath] : undefined;
    if (content === undefined) {
      throw new PlatformError("execution_failed", "The file could not be read.");
    }
    const bytes = Buffer.from(content, "utf8");
    const truncated = bytes.length > maxBytes;
    return {
      content: bytes.subarray(0, maxBytes).toString("utf8"),
      truncated,
      sizeBytes: bytes.length,
    };
  }

  async runCapture(request: CaptureRequest): Promise<CaptureOutcome> {
    this.record("runCapture", { argCount: request.args.length, timeoutMs: request.timeoutMs });
    const script: CaptureScript = this.options.captureScript ?? { kind: "exit", exitCode: 0, output: "" };
    const started = Date.now();
    if (request.signal.aborted) {
      return this.captured("cancelled", "", request, started);
    }
    if (script.kind === "exit") {
      return this.captured("exited", script.output, request, started, script.exitCode);
    }
    const kind = await waitForStop(request);
    return this.captured(kind, script.output, request, started);
  }

  private captured(
    kind: "exited" | "timed_out" | "cancelled",
    output: string,
    request: CaptureRequest,
    started: number,
    exitCode = -1,
  ): CaptureOutcome {
    const tail = tailOfText(output, request.maxOutputBytes);
    const durationMs = Date.now() - started;
    if (kind === "exited") {
      return { kind, exitCode, output: tail.output, truncated: tail.truncated, durationMs };
    }
    return { kind, output: tail.output, truncated: tail.truncated, durationMs };
  }

  systemInfo(): SystemInfo {
    this.record("systemInfo");
    return {
      hostname: "simulated-host",
      os: "simulated",
      os_version: "0.0.0-simulated",
      arch: process.arch,
      cpu_count: os.cpus().length || 1,
      total_memory_mb: 8192,
      free_memory_mb: 4096,
      uptime_seconds: Math.floor(process.uptime()),
      ...this.options.systemInfo,
    };
  }

  now(): Date {
    return this.options.now ? this.options.now() : new Date();
  }
}

/** Resolves "cancelled" on abort, or "timed_out" after the request's timeout, whichever comes first. */
function waitForStop(request: CaptureRequest): Promise<"cancelled" | "timed_out"> {
  return new Promise((resolve) => {
    const finish = (kind: "cancelled" | "timed_out"): void => {
      clearTimeout(timer);
      request.signal.removeEventListener("abort", onAbort);
      resolve(kind);
    };
    const onAbort = (): void => finish("cancelled");
    const timer = setTimeout(() => finish("timed_out"), request.timeoutMs);
    request.signal.addEventListener("abort", onAbort, { once: true });
  });
}

function delay(ms: number): Promise<void> {
  if (ms <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => setTimeout(resolve, ms));
}
