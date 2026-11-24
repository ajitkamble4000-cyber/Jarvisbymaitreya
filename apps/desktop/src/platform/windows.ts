/**
 * Real Windows implementation of the platform adapter.
 *
 * Rules: executables are located only through absolute candidate paths built from environment variables (never a
 * PATH search); processes are started with shell false, windowsHide true, detached true and stdio ignore; a start
 * counts as successful only after the 'spawn' event delivers a pid and a short check shows no immediate error.
 */
import { spawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import {
  PlatformError,
  assertHttpUrl,
  type CaptureOutcome,
  type CaptureRequest,
  type ExecutableLookup,
  type Platform,
  type CreateFilesRequest,
  type CreateFilesResult,
  type ReadFileResult,
  type SpawnRequest,
  type StartApp,
  type SystemInfo,
} from "./platform.js";
import { OutputTail } from "./output-tail.js";
import { writeNewFiles } from "./files.js";

/** How long the Start-menu app list is reused before it is read again. */
export const START_APPS_CACHE_MS = 5 * 60_000;
const START_APPS_TIMEOUT_MS = 20_000;
const START_APPS_MAX_OUTPUT = 2_000_000;
/** The only script the agent runs to list apps. It reads the Start menu and prints JSON; it changes nothing. */
export const START_APPS_SCRIPT =
  "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress";

/** Parses Get-StartApps JSON (one object or an array) into unique, well-formed entries. */
export function parseStartApps(output: string): StartApp[] {
  let value: unknown;
  try {
    value = JSON.parse(output.trim() || "[]");
  } catch {
    throw new PlatformError("execution_failed", "The Start menu app list could not be read.");
  }
  const rows = Array.isArray(value) ? value : [value];
  const seen = new Set<string>();
  const apps: StartApp[] = [];
  for (const row of rows) {
    if (typeof row !== "object" || row === null) {
      continue;
    }
    const name = (row as { Name?: unknown }).Name;
    const appId = (row as { AppID?: unknown }).AppID;
    if (typeof name !== "string" || typeof appId !== "string") {
      continue;
    }
    const cleanName = name.trim();
    if (!cleanName || cleanName.length > 200 || !isSafeAppId(appId) || seen.has(appId)) {
      continue;
    }
    seen.add(appId);
    apps.push({ name: cleanName, appId });
  }
  return apps;
}

/** An AppUserModelID as Windows prints it: no control characters, quotes or line breaks. */
export function isSafeAppId(appId: string): boolean {
  return appId.length > 0 && appId.length <= 512 && !/[\u0000-\u001f"]/.test(appId);
}

export const START_CHECK_MS = 800;
/** After a timeout or cancel, how long to wait for the killed tree to close its output before reporting what was kept. */
const KILL_SETTLE_MS = 1500;
/**
 * The only environment variables an approved check receives. PATH lets the check find its own tools; SystemRoot is the
 * Windows folder that Windows programs need to start and load their system libraries. Nothing else is passed on.
 */
export const CHECK_ENVIRONMENT_NAMES = ["PATH", "SystemRoot"] as const;

const TEMPLATE_VARIABLE = /%([^%]+)%/g;
const CONTROL_CHARS = /[\u0000-\u001f]/;

type EnvMap = Readonly<Record<string, string | undefined>>;

function lookupEnv(env: EnvMap, name: string): string | undefined {
  if (Object.prototype.hasOwnProperty.call(env, name)) {
    return env[name];
  }
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(env)) {
    if (key.toLowerCase() === wanted) {
      return value;
    }
  }
  return undefined;
}

/**
 * Expands %VARIABLE% templates against env and keeps only absolute Windows paths. A template whose variable is
 * missing is dropped rather than guessed. Pure function: it uses path.win32 on every platform so it is testable on Linux.
 */
export function expandCandidatePaths(templates: readonly string[], env: EnvMap): string[] {
  const results: string[] = [];
  for (const template of templates) {
    let missing = false;
    const expanded = template.replace(TEMPLATE_VARIABLE, (_match, name: string) => {
      const value = lookupEnv(env, name);
      if (value === undefined || value.length === 0) {
        missing = true;
        return "";
      }
      return value;
    });
    if (missing || CONTROL_CHARS.test(expanded) || !path.win32.isAbsolute(expanded)) {
      continue;
    }
    const normalised = path.win32.normalize(expanded);
    if (!path.win32.isAbsolute(normalised)) {
      continue;
    }
    if (!results.includes(normalised)) {
      results.push(normalised);
    }
  }
  return results;
}

/** Matches child_process.spawn. Injected by tests so that no real process is started on Linux. */
export type SpawnFunction = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;

export interface WindowsPlatformOptions {
  env?: EnvMap;
  spawnImpl?: SpawnFunction;
  /** Opens the URL through the host (Electron shell.openExternal). Defaults to rundll32 FileProtocolHandler. */
  openExternal?: (url: string) => Promise<void>;
  startCheckMs?: number;
  now?: () => Date;
}

export class WindowsPlatform implements Platform {
  readonly kind = "windows" as const;
  readonly simulated = false;
  readonly banner = null;

  private readonly env: EnvMap;
  private readonly openExternalHook: ((url: string) => Promise<void>) | undefined;
  private readonly startCheckMs: number;
  private readonly clock: () => Date;
  private readonly spawnImpl: SpawnFunction;

  constructor(options: WindowsPlatformOptions = {}) {
    this.env = options.env ?? process.env;
    this.spawnImpl = options.spawnImpl ?? spawn;
    this.openExternalHook = options.openExternal;
    this.startCheckMs = options.startCheckMs ?? START_CHECK_MS;
    this.clock = options.now ?? (() => new Date());
  }

  async findExecutable(lookup: ExecutableLookup): Promise<string | null> {
    for (const candidate of expandCandidatePaths(lookup.candidates, this.env)) {
      try {
        const stats = await fs.promises.stat(candidate);
        if (stats.isFile()) {
          return candidate;
        }
      } catch {
        // Missing candidates are normal: keep looking in the next known location.
      }
    }
    return null;
  }

  spawnDetached(request: SpawnRequest): Promise<{ pid: number }> {
    return startProcess(request, this.startCheckMs, this.spawnImpl);
  }

  async runCapture(request: CaptureRequest): Promise<CaptureOutcome> {
    if (!path.win32.isAbsolute(request.command)) {
      throw new PlatformError("execution_failed", "The check command is not an absolute path.");
    }
    if (!(await isRegularFile(request.command))) {
      throw new PlatformError("not_installed", "The check command was not found as a file.");
    }
    if (!(await this.folderExists(request.cwd))) {
      throw new PlatformError("execution_failed", "The check folder does not exist.");
    }
    const systemRoot = this.env.SystemRoot ?? this.env.SYSTEMROOT ?? this.env.windir;
    const taskkill =
      systemRoot !== undefined && path.win32.isAbsolute(systemRoot)
        ? path.win32.join(systemRoot, "System32", "taskkill.exe")
        : undefined;
    return captureProcess(request, {
      spawnImpl: this.spawnImpl,
      env: checkEnvironment(this.env),
      taskkillPath: taskkill,
      nowMs: () => this.clock().getTime(),
    });
  }

  async openExternal(url: string): Promise<void> {
    assertHttpUrl(url);
    if (this.openExternalHook !== undefined) {
      await this.openExternalHook(url);
      return;
    }
    const systemRoot = this.env.SystemRoot ?? this.env.SYSTEMROOT ?? this.env.windir;
    if (systemRoot === undefined || !path.win32.isAbsolute(systemRoot)) {
      throw new PlatformError("unsupported_platform", "The Windows system folder is unknown.");
    }
    await this.launchWithoutWaiting(
      path.win32.join(systemRoot, "System32", "rundll32.exe"),
      ["url.dll,FileProtocolHandler", url],
    );
  }

  async openFolder(folderPath: string): Promise<void> {
    const systemRoot = this.env.SystemRoot ?? this.env.SYSTEMROOT ?? this.env.windir;
    if (systemRoot === undefined || !path.win32.isAbsolute(systemRoot)) {
      throw new PlatformError("unsupported_platform", "The Windows system folder is unknown.");
    }
    // explorer.exe hands the folder to a running Explorer and then exits with code 1 in many cases, so its exit
    // code says nothing about whether the folder opened. Only a failure to start is reported.
    await this.launchWithoutWaiting(path.win32.join(systemRoot, "explorer.exe"), [folderPath], {
      exitCodeIsFailure: false,
    });
  }

  async folderExists(folderPath: string): Promise<boolean> {
    try {
      return (await fs.promises.stat(folderPath)).isDirectory();
    } catch {
      return false;
    }
  }

  async readFileLimited(filePath: string, maxBytes: number): Promise<ReadFileResult> {
    let entry: fs.Stats;
    try {
      entry = await fs.promises.lstat(filePath);
    } catch {
      throw new PlatformError("execution_failed", "The file could not be read.");
    }
    if (entry.isSymbolicLink()) {
      throw new PlatformError("permission_denied", "Symbolic links cannot be read.");
    }
    if (!entry.isFile()) {
      throw new PlatformError("execution_failed", "The approved item is not a regular file.");
    }
    // A link in a parent folder changes the resolved path. Compare case-insensitively, as Windows does.
    const resolved = path.resolve(await fs.promises.realpath(filePath)).toLowerCase();
    if (resolved !== path.resolve(filePath).toLowerCase()) {
      throw new PlatformError("permission_denied", "The file path passes through a link and cannot be read.");
    }

    const size = entry.size;
    const toRead = Math.min(size, maxBytes);
    const buffer = Buffer.alloc(toRead);
    const handle = await fs.promises.open(filePath, "r");
    try {
      let offset = 0;
      while (offset < toRead) {
        const { bytesRead } = await handle.read(buffer, offset, toRead - offset, offset);
        if (bytesRead === 0) {
          break;
        }
        offset += bytesRead;
      }
      return {
        content: buffer.subarray(0, offset).toString("utf8"),
        truncated: size > maxBytes,
        sizeBytes: size,
      };
    } finally {
      await handle.close();
    }
  }

  systemInfo(): SystemInfo {
    const megabyte = 1024 * 1024;
    return {
      hostname: os.hostname(),
      os: "windows",
      os_version: os.release(),
      arch: process.arch,
      cpu_count: os.cpus().length,
      total_memory_mb: Math.round(os.totalmem() / megabyte),
      free_memory_mb: Math.round(os.freemem() / megabyte),
      uptime_seconds: Math.floor(os.uptime()),
    };
  }

  private startAppsCache: { at: number; apps: StartApp[] } | null = null;

  async listStartApps(signal: AbortSignal): Promise<StartApp[]> {
    const nowMs = this.clock().getTime();
    if (this.startAppsCache !== null && nowMs - this.startAppsCache.at < START_APPS_CACHE_MS) {
      return this.startAppsCache.apps;
    }
    const systemRoot = this.systemRoot();
    const powershell = path.win32.join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
    const env: Record<string, string> = {};
    for (const [key, value] of Object.entries(this.env)) {
      if (typeof value === "string") {
        env[key] = value;
      }
    }
    const outcome = await captureProcess(
      {
        command: powershell,
        args: ["-NoProfile", "-NonInteractive", "-Command", START_APPS_SCRIPT],
        cwd: systemRoot,
        timeoutMs: START_APPS_TIMEOUT_MS,
        signal,
        maxOutputBytes: START_APPS_MAX_OUTPUT,
      },
      {
        spawnImpl: this.spawnImpl,
        env,
        taskkillPath: path.win32.join(systemRoot, "System32", "taskkill.exe"),
        nowMs: () => this.clock().getTime(),
      },
    );
    if (outcome.kind === "cancelled") {
      throw new PlatformError("cancelled", "Reading the Start menu was cancelled.");
    }
    if (outcome.kind === "timed_out" || outcome.exitCode !== 0 || outcome.truncated) {
      throw new PlatformError("execution_failed", "The Start menu app list could not be read.");
    }
    const apps = parseStartApps(outcome.output);
    this.startAppsCache = { at: nowMs, apps };
    return apps;
  }

  async launchStartApp(appId: string, signal: AbortSignal, onStarted: () => void): Promise<void> {
    if (!isSafeAppId(appId)) {
      throw new PlatformError("execution_failed", "That app id cannot be started.");
    }
    // explorer.exe opens shell:AppsFolder entries for desktop and Store apps alike. Its exit code means nothing.
    await startProcess(
      {
        executable: path.win32.join(this.systemRoot(), "explorer.exe"),
        args: [`shell:AppsFolder\\${appId}`],
        signal,
        onStarted: () => onStarted(),
        exitCodeIsFailure: false,
      },
      this.startCheckMs,
      this.spawnImpl,
    );
  }

  createFiles(request: CreateFilesRequest): Promise<CreateFilesResult> {
    return writeNewFiles(request);
  }

  now(): Date {
    return this.clock();
  }

  private systemRoot(): string {
    const systemRoot = this.env.SystemRoot ?? this.env.SYSTEMROOT ?? this.env.windir;
    if (systemRoot === undefined || !path.win32.isAbsolute(systemRoot)) {
      throw new PlatformError("unsupported_platform", "The Windows system folder is unknown.");
    }
    return systemRoot;
  }

  private async launchWithoutWaiting(
    executable: string,
    args: readonly string[],
    options: { exitCodeIsFailure?: boolean } = {},
  ): Promise<void> {
    await startProcess(
      {
        executable,
        args,
        signal: new AbortController().signal,
        onStarted: () => undefined,
        exitCodeIsFailure: options.exitCodeIsFailure ?? true,
      },
      this.startCheckMs,
      this.spawnImpl,
    );
  }
}

/** The environment for an approved check: CHECK_ENVIRONMENT_NAMES only, each taken from the agent's own environment. */
export function checkEnvironment(env: EnvMap): Record<string, string> {
  const result: Record<string, string> = {};
  for (const name of CHECK_ENVIRONMENT_NAMES) {
    const value = lookupEnv(env, name);
    if (value !== undefined && value.length > 0) {
      result[name] = value;
    }
  }
  return result;
}

async function isRegularFile(filePath: string): Promise<boolean> {
  try {
    return (await fs.promises.stat(filePath)).isFile();
  } catch {
    return false;
  }
}

interface CaptureOptions {
  spawnImpl: SpawnFunction;
  env: Record<string, string>;
  /** Absolute path to taskkill.exe, used to kill the whole process tree. Undefined when the system folder is unknown. */
  taskkillPath: string | undefined;
  nowMs: () => number;
}

/**
 * Runs one check with shell false, windowsHide true, no detached process, and stdout and stderr both piped into one
 * tail buffer. A timeout or an abort kills the process tree (taskkill /T /F /PID, then a direct kill), then reports
 * what was captured. Rejects with a PlatformError only when the process cannot be created.
 */
export function captureProcess(request: CaptureRequest, options: CaptureOptions): Promise<CaptureOutcome> {
  return new Promise((resolve, reject) => {
    const startedAt = options.nowMs();
    const elapsed = (): number => Math.max(0, options.nowMs() - startedAt);
    if (request.signal.aborted) {
      resolve({ kind: "cancelled", output: "", truncated: false, durationMs: 0 });
      return;
    }

    let child: ChildProcess;
    try {
      child = options.spawnImpl(request.command, [...request.args], {
        cwd: request.cwd,
        env: options.env,
        shell: false,
        windowsHide: true,
        detached: false,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch {
      reject(new PlatformError("execution_failed", "The check could not be started."));
      return;
    }

    const tail = new OutputTail(request.maxOutputBytes);
    child.stdout?.on("data", (chunk: Buffer) => tail.push(chunk));
    child.stderr?.on("data", (chunk: Buffer) => tail.push(chunk));

    let settled = false;
    let stopped: "timed_out" | "cancelled" | null = null;
    let fallback: NodeJS.Timeout | undefined;

    const clearTimers = (): void => {
      clearTimeout(timer);
      if (fallback !== undefined) {
        clearTimeout(fallback);
      }
      request.signal.removeEventListener("abort", onAbort);
    };
    const finish = (outcome: CaptureOutcome): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimers();
      resolve(outcome);
    };
    const captured = (kind: "timed_out" | "cancelled"): CaptureOutcome => ({
      kind,
      output: tail.text(),
      truncated: tail.truncated,
      durationMs: elapsed(),
    });
    const stop = (reason: "timed_out" | "cancelled"): void => {
      if (stopped !== null || settled) {
        return;
      }
      stopped = reason;
      killProcessTree(child, options);
      fallback = setTimeout(() => finish(captured(reason)), KILL_SETTLE_MS);
    };
    const onAbort = (): void => stop("cancelled");
    const timer = setTimeout(() => stop("timed_out"), request.timeoutMs);
    request.signal.addEventListener("abort", onAbort, { once: true });

    child.once("error", (error: NodeJS.ErrnoException) => {
      if (child.pid !== undefined || settled) {
        // After a spawn the close event still arrives or the stop timers finish the run.
        return;
      }
      settled = true;
      clearTimers();
      reject(
        new PlatformError(
          error.code === "ENOENT" ? "not_installed" : "execution_failed",
          "The check could not be started.",
        ),
      );
    });
    child.once("close", (code: number | null) => {
      if (stopped !== null) {
        finish(captured(stopped));
        return;
      }
      finish({ kind: "exited", exitCode: code ?? -1, output: tail.text(), truncated: tail.truncated, durationMs: elapsed() });
    });
  });
}

/** Kills the process and its children. taskkill /T ends the tree; the direct kill is a fallback if taskkill is missing. */
function killProcessTree(child: ChildProcess, options: CaptureOptions): void {
  if (child.pid !== undefined && options.taskkillPath !== undefined) {
    try {
      const killer = options.spawnImpl(options.taskkillPath, ["/T", "/F", "/PID", String(child.pid)], {
        shell: false,
        windowsHide: true,
        stdio: "ignore",
      });
      killer.on("error", () => undefined);
    } catch {
      // The direct kill below still runs.
    }
  }
  child.kill();
}

/**
 * Starts a detached process with shell false. Settles as follows:
 * - 'error' before a pid (for example ENOENT): not_installed (ENOENT) or execution_failed.
 * - abort before the pid: cancelled, and the child is killed.
 * - 'spawn' delivers the pid: onStarted runs, then the check window starts.
 * - a non-zero exit inside the window: execution_failed. Otherwise resolve with the pid.
 */
function startProcess(
  request: SpawnRequest,
  checkMs: number,
  spawnFn: SpawnFunction,
): Promise<{ pid: number }> {
  return new Promise((resolve, reject) => {
    if (request.signal.aborted) {
      reject(new PlatformError("cancelled", "The start was cancelled before the process was created."));
      return;
    }

    let child: ChildProcess;
    try {
      child = spawnFn(request.executable, [...request.args], {
        shell: false,
        windowsHide: true,
        detached: true,
        stdio: "ignore",
      });
    } catch {
      reject(new PlatformError("execution_failed", "The application could not be started."));
      return;
    }

    let phase: "starting" | "checking" | "settled" = "starting";
    let timer: NodeJS.Timeout | undefined;
    let pid: number | undefined;

    const settle = (action: () => void): void => {
      if (phase === "settled") {
        return;
      }
      phase = "settled";
      if (timer !== undefined) {
        clearTimeout(timer);
      }
      request.signal.removeEventListener("abort", onAbort);
      action();
    };

    const onAbort = (): void => {
      if (phase !== "starting") {
        return;
      }
      child.kill();
      settle(() =>
        reject(new PlatformError("cancelled", "The start was cancelled before the process was created.")),
      );
    };
    request.signal.addEventListener("abort", onAbort, { once: true });

    child.once("error", (error: NodeJS.ErrnoException) => {
      settle(() =>
        reject(
          new PlatformError(
            error.code === "ENOENT" ? "not_installed" : "execution_failed",
            "The application could not be started.",
          ),
        ),
      );
    });

    child.once("spawn", () => {
      if (phase !== "starting") {
        return;
      }
      if (child.pid === undefined) {
        settle(() => reject(new PlatformError("execution_failed", "The application did not report a process id.")));
        return;
      }
      pid = child.pid;
      phase = "checking";
      request.onStarted(pid);
      timer = setTimeout(() => {
        settle(() => resolve({ pid: pid as number }));
      }, checkMs);
    });

    child.once("exit", (code: number | null) => {
      if (phase === "checking" && code !== 0 && request.exitCodeIsFailure !== false) {
        settle(() =>
          reject(new PlatformError("execution_failed", "The application exited while it was starting.")),
        );
      }
    });

    child.unref();
  });
}
