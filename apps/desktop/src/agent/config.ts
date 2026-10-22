/**
 * agent.json: the local authority for what may run. The backend can name only ids; every id is resolved here,
 * and every path, executable and URL comes from this file alone.
 */
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { z } from "zod";
import { RESOURCE_ID_PATTERN, SUPPORTED_ACTIONS, type ActionName, type Resources } from "../shared/protocol.js";
import { readTextIfExists, writePrivateFileAtomic } from "./private-files.js";
import { silentLogger, type Logger } from "./logger.js";

export const CONFIG_FILE_NAME = "agent.json";
export const APP_FOLDER_NAME = "JARVIS";
const MAX_CONFIG_BYTES = 256 * 1024;
const CHECK_ARG_MAX_COUNT = 32;
const CHECK_ARG_MAX_LENGTH = 200;
const CHECK_TIMEOUT_MIN_SECONDS = 10;
const CHECK_TIMEOUT_MAX_SECONDS = 600;
const CHECK_TIMEOUT_DEFAULT_SECONDS = 300;

/** Actions switched on or off by a flag in agent.json. run_check is not a flag: it is enabled by its approved checks. */
export type FlagAction = Exclude<ActionName, "run_check">;
const FLAG_ACTIONS = SUPPORTED_ACTIONS.filter((name): name is FlagAction => name !== "run_check");

const label = z.string().min(1).max(120).regex(/^[^\u0000-\u001f\u007f]+$/);
const resourceId = z.string().regex(RESOURCE_ID_PATTERN);
const candidate = z.string().min(1).max(1024).regex(/^[^\u0000-\u001f]+$/);
const localPath = z.string().min(1).max(4096);

/** A missing capability key means disabled. Unknown keys are rejected. */
const capabilitiesSchema = z
  .object(Object.fromEntries(FLAG_ACTIONS.map((name) => [name, z.boolean().optional()])))
  .strict()
  .transform(
    (value): Record<FlagAction, boolean> =>
      Object.fromEntries(FLAG_ACTIONS.map((name) => [name, value[name] === true])) as Record<FlagAction, boolean>,
  );

/** Windows-style absolute test. It also accepts rooted POSIX paths, so the core runs the same way in tests on Linux. */
function isAbsoluteLocalPath(value: string): boolean {
  return path.win32.isAbsolute(value) && !/[\u0000-\u001f]/.test(value);
}

/**
 * One approved project check. The command line lives only here, on the device: the backend names a check by id and
 * the agent runs exactly this argv, with no shell. Unknown keys are refused, so no other field can carry a command.
 */
const ApprovedCheckSchema = z
  .object({
    id: resourceId,
    label: label.refine((value) => value.trim().length > 0, "label must not be blank"),
    command: candidate.refine(isAbsoluteLocalPath, "command must be an absolute path"),
    args: z
      .array(
        z
          .string()
          .max(CHECK_ARG_MAX_LENGTH)
          .refine((value) => !value.includes("\u0000"), "args must not contain NUL"),
      )
      .max(CHECK_ARG_MAX_COUNT)
      .default([]),
    cwd: candidate.refine(isAbsoluteLocalPath, "cwd must be an absolute path"),
    timeoutSeconds: z
      .number()
      .int()
      .min(CHECK_TIMEOUT_MIN_SECONDS)
      .max(CHECK_TIMEOUT_MAX_SECONDS)
      .default(CHECK_TIMEOUT_DEFAULT_SECONDS),
  })
  .strict();

/** The file as read. approvedChecks is still raw here: parseConfigDetailed validates each entry. */
export const AgentConfigSchema = z
  .object({
    version: z.literal(1),
    approvedApps: z
      .array(
        z.object({
          id: resourceId,
          label,
          windows: z.object({ candidates: z.array(candidate).max(16) }),
        }),
      )
      .max(100),
    approvedUrls: z
      .array(
        z.object({
          id: resourceId,
          label,
          url: z
            .string()
            .min(8)
            .max(2048)
            .refine((value) => /^https?:\/\//.test(value), "URL must start with http:// or https://"),
        }),
      )
      .max(100),
    approvedFolders: z.array(z.object({ id: resourceId, label, path: localPath })).max(100),
    approvedProjects: z
      .array(z.object({ id: resourceId, label, path: localPath, app_id: resourceId.optional() }))
      .max(100),
    approvedFiles: z.array(z.object({ id: resourceId, label, path: localPath })).max(100),
    capabilities: capabilitiesSchema,
    // Entries are validated one by one in parseConfigDetailed: an invalid entry is skipped, not run and not fatal.
    approvedChecks: z.array(z.unknown()).max(100).default([]),
    pairing: z
      .object({
        deviceId: z.string().min(1).max(64),
        deviceName: label,
        serverUrl: z.string().min(8).max(2048),
        websocketPath: z.string().regex(/^\/[A-Za-z0-9/_-]{1,200}$/),
        heartbeatIntervalSeconds: z.number().int().min(1).max(3600),
        protocolVersion: z.number().int(),
        pairedAt: z.string().min(1).max(64),
      })
      .nullable(),
  })
  .strict();

/** The validated config. approvedChecks holds only entries that passed ApprovedCheckSchema. */
export type AgentConfig = Omit<z.infer<typeof AgentConfigSchema>, "approvedChecks"> & {
  approvedChecks: ApprovedCheck[];
};
export type PairingRecord = NonNullable<AgentConfig["pairing"]>;
export type ApprovedApp = AgentConfig["approvedApps"][number];
export type ApprovedUrl = AgentConfig["approvedUrls"][number];
export type ApprovedFolder = AgentConfig["approvedFolders"][number];
export type ApprovedProject = AgentConfig["approvedProjects"][number];
export type ApprovedFile = AgentConfig["approvedFiles"][number];
export type ApprovedCheck = z.infer<typeof ApprovedCheckSchema>;

export interface SkippedCheck {
  /** Position in the approvedChecks array of agent.json. */
  index: number;
  /** Field names and rules only. Values from the file are never included. */
  reason: string;
}

export interface ParsedConfig {
  config: AgentConfig;
  skippedChecks: SkippedCheck[];
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

/** Defaults from the brief: Chrome and VS Code, found only through known install folders. */
export function defaultConfig(): AgentConfig {
  return {
    version: 1,
    approvedApps: [
      {
        id: "chrome",
        label: "Google Chrome",
        windows: {
          candidates: [
            "%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe",
            "%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe",
            "%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe",
          ],
        },
      },
      {
        id: "vscode",
        label: "Visual Studio Code",
        windows: {
          candidates: [
            "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
            "%ProgramFiles%\\Microsoft VS Code\\Code.exe",
            "%ProgramFiles(x86)%\\Microsoft VS Code\\Code.exe",
          ],
        },
      },
    ],
    approvedUrls: [],
    approvedFolders: [],
    approvedProjects: [],
    approvedFiles: [],
    capabilities: {
      open_url: true,
      launch_app: true,
      open_folder: true,
      open_project: true,
      system_info: true,
      read_file: true,
      app_availability: true,
    },
    approvedChecks: [],
    pairing: null,
  };
}

/**
 * Parses and validates the config text. Error messages never echo the file contents. An approved check that fails
 * validation is left out and reported in skippedChecks; the rest of the file still loads.
 */
export function parseConfigDetailed(text: string): ParsedConfig {
  if (Buffer.byteLength(text, "utf8") > MAX_CONFIG_BYTES) {
    throw new ConfigError("agent.json is too large.");
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new ConfigError("agent.json is not valid JSON.");
  }
  const parsed = AgentConfigSchema.safeParse(value);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    const where = first === undefined ? "" : ` (${first.path.join(".")})`;
    throw new ConfigError(`agent.json failed validation${where}.`);
  }
  const { checks, skipped } = validateChecks(parsed.data.approvedChecks);
  const config: AgentConfig = { ...parsed.data, approvedChecks: checks };
  assertUniqueIds(config);
  return { config, skippedChecks: skipped };
}

export function parseConfig(text: string): AgentConfig {
  return parseConfigDetailed(text).config;
}

/**
 * Keeps the entries that pass validation. A later entry that reuses an id is skipped, so every id names exactly one
 * command. Nothing from a skipped entry is ever run.
 */
function validateChecks(raw: readonly unknown[]): { checks: ApprovedCheck[]; skipped: SkippedCheck[] } {
  const checks: ApprovedCheck[] = [];
  const skipped: SkippedCheck[] = [];
  const seen = new Set<string>();
  raw.forEach((entry, index) => {
    const result = ApprovedCheckSchema.safeParse(entry);
    if (!result.success) {
      const first = result.error.issues[0];
      const field = first === undefined ? "" : first.path.join(".");
      const reason = first?.code === "unrecognized_keys" ? "unknown field" : `${field || "entry"} failed validation`;
      skipped.push({ index, reason });
      return;
    }
    if (seen.has(result.data.id)) {
      skipped.push({ index, reason: "duplicate id" });
      return;
    }
    seen.add(result.data.id);
    checks.push(result.data);
  });
  return { checks, skipped };
}

function assertUniqueIds(config: AgentConfig): void {
  const groups: Array<[string, Array<{ id: string }>]> = [
    ["approvedApps", config.approvedApps],
    ["approvedUrls", config.approvedUrls],
    ["approvedFolders", config.approvedFolders],
    ["approvedProjects", config.approvedProjects],
    ["approvedFiles", config.approvedFiles],
  ];
  for (const [name, entries] of groups) {
    const seen = new Set<string>();
    for (const entry of entries) {
      if (seen.has(entry.id)) {
        throw new ConfigError(`agent.json has a duplicate id in ${name}.`);
      }
      seen.add(entry.id);
    }
  }
  for (const project of config.approvedProjects) {
    if (project.app_id !== undefined && !config.approvedApps.some((app) => app.id === project.app_id)) {
      throw new ConfigError("An approved project refers to an app that is not approved.");
    }
  }
}

/**
 * Loads agent.json. A missing file means defaults; nothing is written until the config is saved.
 * A check whose command is not an existing file, or whose cwd is not an existing folder, is skipped at load time
 * and logged, so it can never be run. Skipped entries are logged by index and reason only.
 */
export async function loadConfig(file: string, logger: Logger = silentLogger): Promise<AgentConfig> {
  const text = await readTextIfExists(file);
  if (text === null) {
    return defaultConfig();
  }
  const { config, skippedChecks } = parseConfigDetailed(text);
  for (const skipped of skippedChecks) {
    logger.warn("Skipped an approved check that failed validation.", { index: skipped.index, reason: skipped.reason });
  }
  const present: ApprovedCheck[] = [];
  for (const [index, check] of config.approvedChecks.entries()) {
    const problem = await checkPathProblem(check);
    if (problem === null) {
      present.push(check);
    } else {
      logger.warn("Skipped an approved check whose path is missing.", { index, reason: problem });
    }
  }
  return { ...config, approvedChecks: present };
}

/** Returns why the check cannot run from its paths, or null when both exist as the right kind of entry. */
async function checkPathProblem(check: ApprovedCheck): Promise<string | null> {
  try {
    if (!(await fs.stat(check.command)).isFile()) {
      return "command is not a file";
    }
  } catch {
    return "command does not exist";
  }
  try {
    if (!(await fs.stat(check.cwd)).isDirectory()) {
      return "cwd is not a folder";
    }
  } catch {
    return "cwd does not exist";
  }
  return null;
}

export async function saveConfig(file: string, config: AgentConfig): Promise<void> {
  const validated = parseConfig(JSON.stringify(config));
  await writePrivateFileAtomic(file, `${JSON.stringify(validated, null, 2)}\n`);
}

/** run_check is reported only while at least one valid approved check exists. The other actions use their flags. */
export function isCapabilityEnabled(config: AgentConfig, action: ActionName): boolean {
  if (action === "run_check") {
    return config.approvedChecks.length > 0;
  }
  return config.capabilities[action] === true;
}

export function enabledCapabilities(config: AgentConfig): ActionName[] {
  return SUPPORTED_ACTIONS.filter((action) => isCapabilityEnabled(config, action));
}

/**
 * The only resource view that leaves the device: ids and labels, plus URL strings for URL entries. A check is shown by
 * id and label only; its command, arguments and folder never leave the device.
 */
export function resourcesForServer(config: AgentConfig): Resources {
  return {
    apps: config.approvedApps.map((app) => ({ id: app.id, label: app.label })),
    urls: config.approvedUrls.map((entry) => ({ id: entry.id, label: entry.label, url: entry.url })),
    folders: config.approvedFolders.map((entry) => ({ id: entry.id, label: entry.label })),
    projects: config.approvedProjects.map((entry) => ({ id: entry.id, label: entry.label })),
    files: config.approvedFiles.map((entry) => ({ id: entry.id, label: entry.label })),
    checks: config.approvedChecks.map((entry) => ({ id: entry.id, label: entry.label })),
  };
}

/** Folder that holds agent.json: %APPDATA%\JARVIS on Windows, ~/.config/JARVIS elsewhere. */
export function defaultDataDir(env: NodeJS.ProcessEnv = process.env): string {
  if (process.platform === "win32") {
    const appData = env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming");
    return path.join(appData, APP_FOLDER_NAME);
  }
  const base = env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config");
  return path.join(base, APP_FOLDER_NAME);
}

export function configFilePath(dataDir: string): string {
  return path.join(dataDir, CONFIG_FILE_NAME);
}
