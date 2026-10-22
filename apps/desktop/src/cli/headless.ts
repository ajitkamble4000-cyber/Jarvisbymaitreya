/**
 * Headless runner: pairs with a code from the web app (optional) and runs the agent in the terminal.
 *
 *   npm run headless -- --server http://127.0.0.1:8000 --code XXXX-XXXX --platform simulated
 *
 * --platform simulated (default) records and reports actions without performing them.
 * --platform windows performs real actions on this PC and should only be used on a Windows machine.
 * The device secret is kept in an owner-only file in the data folder. Codes and secrets are never printed.
 */
import { join } from "node:path";
import { Agent, userMessageFor } from "../agent/agent.js";
import { defaultDataDir } from "../agent/config.js";
import { createLogger, type LogSink } from "../agent/logger.js";
import { FileSecretStore } from "../agent/secret-store.js";
import type { Platform } from "../platform/platform.js";
import { SimulatedPlatform } from "../platform/simulated.js";
import { WindowsPlatform } from "../platform/windows.js";

export interface HeadlessArgs {
  server: string | undefined;
  code: string | undefined;
  platform: "simulated" | "windows";
  deviceName: string | undefined;
  dataDir: string | undefined;
  runSeconds: number;
  verbose: boolean;
  help: boolean;
}

export type ParseResult = { ok: true; args: HeadlessArgs } | { ok: false; error: string };

const USAGE = [
  "Usage: npm run headless -- [options]",
  "",
  "  --server <url>        Server origin, for example http://127.0.0.1:8000 (required with --code)",
  "  --code <XXXX-XXXX>    One-time pairing code from the web app",
  "  --platform <name>     simulated (default) or windows",
  "  --device-name <name>  Name shown for this device in the web app",
  "  --data-dir <folder>   Folder for agent.json, outbox.json and secrets.json",
  "  --run-seconds <n>     Stop after n seconds (default 0: run until Ctrl+C)",
  "  --verbose             Also print debug lines",
  "  --help                Show this help",
].join("\n");

const VALUE_FLAGS = new Set(["--server", "--code", "--platform", "--device-name", "--data-dir", "--run-seconds"]);

export function headlessUsage(): string {
  return USAGE;
}

export function parseHeadlessArgs(argv: readonly string[]): ParseResult {
  const args: HeadlessArgs = {
    server: undefined,
    code: undefined,
    platform: "simulated",
    deviceName: undefined,
    dataDir: undefined,
    runSeconds: 0,
    verbose: false,
    help: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index] ?? "";
    if (token === "--help" || token === "-h") {
      args.help = true;
      continue;
    }
    if (token === "--verbose") {
      args.verbose = true;
      continue;
    }
    const equals = token.indexOf("=");
    const flag = equals > 0 ? token.slice(0, equals) : token;
    if (!VALUE_FLAGS.has(flag)) {
      return { ok: false, error: `Unknown option: ${flag.slice(0, 40)}` };
    }
    const value = equals > 0 ? token.slice(equals + 1) : argv[index + 1];
    if (equals <= 0) {
      index += 1;
    }
    if (value === undefined || value.startsWith("--")) {
      return { ok: false, error: `${flag} needs a value.` };
    }
    switch (flag) {
      case "--server":
        args.server = value;
        break;
      case "--code":
        args.code = value;
        break;
      case "--platform":
        if (value !== "simulated" && value !== "windows") {
          return { ok: false, error: "--platform must be simulated or windows." };
        }
        args.platform = value;
        break;
      case "--device-name":
        args.deviceName = value;
        break;
      case "--data-dir":
        args.dataDir = value;
        break;
      case "--run-seconds": {
        const seconds = Number(value);
        if (!Number.isInteger(seconds) || seconds < 0 || seconds > 86_400) {
          return { ok: false, error: "--run-seconds must be a whole number between 0 and 86400." };
        }
        args.runSeconds = seconds;
        break;
      }
    }
  }
  return { ok: true, args };
}

export interface HeadlessIo {
  out: (line: string) => void;
  err: (line: string) => void;
}

const consoleIo: HeadlessIo = {
  out: (line) => process.stdout.write(`${line}\n`),
  err: (line) => process.stderr.write(`${line}\n`),
};

/** Runs the headless agent until stopped. Returns the process exit code. */
export async function runHeadless(
  argv: readonly string[],
  io: HeadlessIo = consoleIo,
  platformOverride?: Platform,
): Promise<number> {
  const parsed = parseHeadlessArgs(argv);
  if (!parsed.ok) {
    io.err(`${parsed.error}\n${USAGE}`);
    return 2;
  }
  const args = parsed.args;
  if (args.help) {
    io.out(USAGE);
    return 0;
  }

  const platform: Platform =
    platformOverride ?? (args.platform === "windows" ? new WindowsPlatform() : new SimulatedPlatform());
  const dataDir = args.dataDir ?? defaultDataDir();
  const sink: LogSink = (level, line) => {
    if (level !== "debug" || args.verbose) {
      io.out(line);
    }
  };
  const agent = new Agent({
    dataDir,
    platform,
    secretStore: new FileSecretStore(join(dataDir, "secrets.json")),
    logger: createLogger(sink),
  });

  if (platform.banner !== null) {
    io.out(`*** ${platform.banner} ***`);
  }
  agent.onStatusChange((status) => {
    const detail = status.message === null ? "" : ` ${status.message}`;
    io.out(`${new Date().toISOString()} status=${status.state}${detail}`);
  });

  await agent.init();

  if (args.code !== undefined) {
    if (args.server === undefined) {
      io.err("Pairing needs --server, for example --server http://127.0.0.1:8000.");
      return 2;
    }
    try {
      await agent.pair({ serverUrl: args.server, code: args.code, deviceName: args.deviceName });
      io.out("Pairing succeeded.");
    } catch (error) {
      io.err(`Pairing failed: ${userMessageFor(error)}`);
      return 1;
    }
  } else if (!agent.getConfigSummary().paired) {
    io.err("This PC is not paired. Pass --server and --code with a code from the web app.");
    return 2;
  }

  await agent.start();
  return waitUntilStopped(args.runSeconds, async () => {
    await agent.stop();
    io.out(`${new Date().toISOString()} agent stopped`);
  });
}

function waitUntilStopped(runSeconds: number, stop: () => Promise<void>): Promise<number> {
  return new Promise((resolve) => {
    let finished = false;
    const finish = (): void => {
      if (finished) {
        return;
      }
      finished = true;
      process.removeListener("SIGINT", finish);
      process.removeListener("SIGTERM", finish);
      void stop().then(() => resolve(0));
    };
    process.once("SIGINT", finish);
    process.once("SIGTERM", finish);
    if (runSeconds > 0) {
      setTimeout(finish, runSeconds * 1000).unref();
    }
  });
}

if (require.main === module) {
  runHeadless(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch(() => {
      process.stderr.write("The headless agent stopped with an unexpected error.\n");
      process.exitCode = 1;
    });
}
