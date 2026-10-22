/**
 * Platform adapter. The agent core only talks to this interface, so the whole core runs on Linux in tests
 * and in the headless runner. WindowsPlatform performs real actions; SimulatedPlatform records calls and
 * performs nothing.
 */

export type PlatformErrorCode =
  | "not_installed"
  | "execution_failed"
  | "permission_denied"
  | "cancelled"
  | "unsupported_platform";

export class PlatformError extends Error {
  readonly code: PlatformErrorCode;

  constructor(code: PlatformErrorCode, message: string) {
    super(message);
    this.name = "PlatformError";
    this.code = code;
  }
}

export interface ExecutableLookup {
  readonly id: string;
  /** Absolute paths or environment-variable templates such as "%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe". */
  readonly candidates: readonly string[];
}

export interface SpawnRequest {
  executable: string;
  args: readonly string[];
  /** Aborting before the spawn event cancels the start. After it, the process is running and cannot be undone. */
  signal: AbortSignal;
  /** Called exactly once with the pid, when the 'spawn' event delivers it. */
  onStarted: (pid: number) => void;
  /** Default true: a non-zero exit inside the start check window is a failed start. False for launchers whose exit code means nothing. */
  exitCodeIsFailure?: boolean;
}

/**
 * One approved check run. The command is an absolute path and args is an argv array: nothing here goes through a
 * shell, and the platform never searches PATH for the command.
 */
export interface CaptureRequest {
  command: string;
  args: readonly string[];
  cwd: string;
  timeoutMs: number;
  signal: AbortSignal;
  /** At most this many bytes of the combined stdout and stderr are kept: the last bytes written. */
  maxOutputBytes: number;
}

/** How a capture ended. The action turns each kind into a result; the platform reports only what happened. */
export type CaptureOutcome =
  | { kind: "exited"; exitCode: number; output: string; truncated: boolean; durationMs: number }
  | { kind: "timed_out"; output: string; truncated: boolean; durationMs: number }
  | { kind: "cancelled"; output: string; truncated: boolean; durationMs: number };

export interface SystemInfo {
  hostname: string;
  os: string;
  os_version: string;
  arch: string;
  cpu_count: number;
  total_memory_mb: number;
  free_memory_mb: number;
  uptime_seconds: number;
}

export interface ReadFileResult {
  content: string;
  truncated: boolean;
  sizeBytes: number;
}

export interface Platform {
  readonly kind: "windows" | "simulated";
  readonly simulated: boolean;
  /** Banner text for the UI, or null when the platform performs real actions. */
  readonly banner: string | null;
  /** Returns an absolute executable path from the lookup candidates, or null when none exists. Never searches PATH. */
  findExecutable(lookup: ExecutableLookup): Promise<string | null>;
  /** Starts a process without a shell. Resolves with the pid only after the start check passes. */
  spawnDetached(request: SpawnRequest): Promise<{ pid: number }>;
  /** Asks the operating system to open an http or https URL in the default browser. */
  openExternal(url: string): Promise<void>;
  /** Opens an approved folder in the file manager. */
  openFolder(folderPath: string): Promise<void>;
  folderExists(folderPath: string): Promise<boolean>;
  /** Reads at most maxBytes of a regular file that is not a symbolic link. */
  readFileLimited(filePath: string, maxBytes: number): Promise<ReadFileResult>;
  /**
   * Runs an approved check to completion, timeout or cancel and returns its captured output. A timeout or a cancel
   * kills the whole process tree. Rejects with a PlatformError when the process cannot be started.
   */
  runCapture(request: CaptureRequest): Promise<CaptureOutcome>;
  systemInfo(): SystemInfo;
  now(): Date;
}

/** Only http and https URLs may leave the agent. Throws a PlatformError otherwise. */
export function assertHttpUrl(url: string): void {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new PlatformError("execution_failed", "The URL is not valid.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new PlatformError("execution_failed", "Only http and https URLs can be opened.");
  }
}
