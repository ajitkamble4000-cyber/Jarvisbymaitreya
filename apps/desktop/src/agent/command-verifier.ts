/**
 * Command verification: steps 1 to 7 of section 4 of docs/desktop-protocol.md, in that order. A command that fails any
 * step is refused and never reaches a handler. Every path, executable and URL in the result comes from the local config.
 */
import { verifyFields } from "../shared/canonical.js";
import {
  ActionArgSchemas,
  CANCEL_ACTION,
  CancelSchema,
  CommandSchema,
  PROTOCOL_VERSION,
  isSupportedAction,
  type ActionName,
  type AgentErrorCode,
} from "../shared/protocol.js";
import type { z } from "zod";
import {
  isCapabilityEnabled,
  type AgentConfig,
  type ApprovedApp,
  type ApprovedCheck,
  type ApprovedFile,
  type ApprovedFolder,
  type ApprovedProject,
  type ApprovedUrl,
} from "./config.js";
import type { ReplayCache } from "./replay-store.js";

export { ReplayCache, REPLAY_WINDOW_MS } from "./replay-store.js";

export const CLOCK_SKEW_MS = 30_000;
/**
 * Longest command lifetime the agent accepts (expires_at - issued_at). The backend caps device command TTL at 540 s
 * (services/api app/core/config.py, device_command_ttl_seconds). With the clock skew this stays well inside
 * REPLAY_WINDOW_MS, so the replay store always remembers a command for as long as it is valid.
 */
export const MAX_COMMAND_LIFETIME_MS = 540_000;
const DEFAULT_PROJECT_APP_ID = "vscode";

/** The local entry a command resolved to. Handlers receive only this, never raw ids or paths from the server. */
export type ResolvedTarget =
  | { action: "launch_app"; app: ApprovedApp }
  | { action: "app_availability"; app: ApprovedApp }
  | { action: "open_url"; url: ApprovedUrl }
  | { action: "open_folder"; folder: ApprovedFolder }
  | { action: "open_project"; project: ApprovedProject; app: ApprovedApp }
  | { action: "read_file"; file: ApprovedFile }
  | { action: "run_check"; check: ApprovedCheck }
  | { action: "system_info" };

export type TargetOf<A extends ActionName> = Extract<ResolvedTarget, { action: A }>;

export interface VerifiedCommand {
  id: string;
  action: ActionName;
  target: ResolvedTarget;
  issuedAt: number;
  expiresAt: number;
}

export type VerifyResult =
  | { ok: true; command: VerifiedCommand }
  | { ok: false; code: AgentErrorCode; message: string; commandId: string | null };

export type CancelVerifyResult = { ok: true; commandId: string } | { ok: false; reason: string };

export interface VerifyContext {
  secret: string;
  config: AgentConfig;
  nowMs: number;
  replay: ReplayCache;
}

type ParsedJson = { ok: true; value: unknown } | { ok: false };

function parseJson(text: string): ParsedJson {
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

function idOf(value: unknown): string | null {
  if (typeof value === "object" && value !== null && "id" in value) {
    const id = (value as { id: unknown }).id;
    return typeof id === "string" && id.length > 0 && id.length <= 128 ? id : null;
  }
  return null;
}

function checkWindow(issuedAt: string, expiresAt: string, nowMs: number): AgentErrorCode | "bad_timestamp" | null {
  const issuedMs = Date.parse(issuedAt);
  const expiresMs = Date.parse(expiresAt);
  if (Number.isNaN(issuedMs) || Number.isNaN(expiresMs)) {
    return "bad_timestamp";
  }
  if (expiresMs - issuedMs > MAX_COMMAND_LIFETIME_MS) {
    return "bad_timestamp";
  }
  if (nowMs >= expiresMs) {
    return "expired";
  }
  if (issuedMs > nowMs + CLOCK_SKEW_MS) {
    return "bad_timestamp";
  }
  return null;
}

/** Resolves the action's arguments against the local config. Returns null when any id is not approved. */
export function resolveTarget(
  action: ActionName,
  args: Record<string, unknown>,
  config: AgentConfig,
): ResolvedTarget | null {
  const findApp = (id: unknown): ApprovedApp | undefined =>
    config.approvedApps.find((app) => app.id === id);
  switch (action) {
    case "launch_app": {
      const app = findApp(args.app_id);
      return app === undefined ? null : { action, app };
    }
    case "app_availability": {
      const app = findApp(args.app_id);
      return app === undefined ? null : { action, app };
    }
    case "open_url": {
      const url =
        typeof args.url_id === "string"
          ? config.approvedUrls.find((entry) => entry.id === args.url_id)
          : config.approvedUrls.find((entry) => entry.url === args.url);
      return url === undefined ? null : { action, url };
    }
    case "open_folder": {
      const folder = config.approvedFolders.find((entry) => entry.id === args.folder_id);
      return folder === undefined ? null : { action, folder };
    }
    case "open_project": {
      const project = config.approvedProjects.find((entry) => entry.id === args.project_id);
      if (project === undefined) {
        return null;
      }
      const appId =
        typeof args.app_id === "string" ? args.app_id : (project.app_id ?? DEFAULT_PROJECT_APP_ID);
      const app = findApp(appId);
      return app === undefined ? null : { action, project, app };
    }
    case "read_file": {
      const file = config.approvedFiles.find((entry) => entry.id === args.file_id);
      return file === undefined ? null : { action, file };
    }
    case "run_check": {
      const check = config.approvedChecks.find((entry) => entry.id === args.check_id);
      return check === undefined ? null : { action, check };
    }
    case "system_info":
      return { action };
  }
}

/**
 * Verifies one inbound command frame, in protocol order:
 * 1 parse and protocol version, 2 signature, 3 expiry and clock skew, 4 replay, 5 action and capability,
 * 6 arguments schema (extra keys rejected), 7 resource ids against the local config.
 */
export function verifyCommand(text: string, ctx: VerifyContext): VerifyResult {
  // Step 1: parse the frame.
  const json = parseJson(text);
  if (!json.ok) {
    return { ok: false, code: "invalid_arguments", message: "Command frame is not valid JSON.", commandId: null };
  }
  const commandId = idOf(json.value);
  const parsed = CommandSchema.safeParse(json.value);
  if (!parsed.success) {
    return { ok: false, code: "invalid_arguments", message: "Command frame is malformed.", commandId };
  }
  const cmd = parsed.data;
  if (cmd.protocol_version !== PROTOCOL_VERSION) {
    return { ok: false, code: "invalid_arguments", message: "Unsupported protocol version.", commandId: cmd.id };
  }

  // Step 2: signature over the exact wire strings, compared in constant time.
  const signatureOk = verifyFields(
    ctx.secret,
    {
      id: cmd.id,
      action: cmd.action,
      issued_at: cmd.issued_at,
      expires_at: cmd.expires_at,
      nonce: cmd.nonce,
      args_json: cmd.args_json,
    },
    cmd.signature,
  );
  if (!signatureOk) {
    // The id in a frame that fails its signature is not authenticated, so it is never echoed back.
    return { ok: false, code: "signature_invalid", message: "Command could not be verified.", commandId: null };
  }

  // Step 3: expiry and clock skew.
  const windowProblem = checkWindow(cmd.issued_at, cmd.expires_at, ctx.nowMs);
  if (windowProblem === "expired") {
    return { ok: false, code: "expired", message: "Command has expired.", commandId: cmd.id };
  }
  if (windowProblem !== null) {
    return {
      ok: false,
      code: "invalid_arguments",
      message: "Command timestamps are outside the allowed window.",
      commandId: cmd.id,
    };
  }

  // Step 4: replay of the id or the nonce. While the replay store is degraded, a command issued before its floor
  // cannot be told apart from a replay, so it is refused. The command is recorded before any later step can refuse it,
  // so a refused command cannot be accepted later under a different verdict.
  const issuedMs = Date.parse(cmd.issued_at);
  const floorMs = ctx.replay.issuedFloorAt(ctx.nowMs);
  if (floorMs !== null && issuedMs < floorMs) {
    return {
      ok: false,
      code: "replayed",
      message: "Command was issued before replay protection could be verified.",
      commandId: cmd.id,
    };
  }
  if (ctx.replay.hasId(cmd.id, ctx.nowMs) || ctx.replay.hasNonce(cmd.nonce, ctx.nowMs)) {
    return { ok: false, code: "replayed", message: "Command was already received.", commandId: cmd.id };
  }
  ctx.replay.remember({ id: cmd.id, nonce: cmd.nonce }, ctx.nowMs);

  // Step 5: supported action and its capability.
  if (!isSupportedAction(cmd.action)) {
    return { ok: false, code: "unsupported_action", message: "Action is not supported.", commandId: cmd.id };
  }
  const action: ActionName = cmd.action;
  if (!isCapabilityEnabled(ctx.config, action)) {
    return { ok: false, code: "permission_denied", message: "This action is not enabled on this device.", commandId: cmd.id };
  }

  // Step 6: arguments against the action schema. Extra keys are rejected by the strict schemas.
  const argsJson = parseJson(cmd.args_json);
  if (!argsJson.ok || typeof argsJson.value !== "object" || argsJson.value === null || Array.isArray(argsJson.value)) {
    return { ok: false, code: "invalid_arguments", message: "Arguments are not a JSON object.", commandId: cmd.id };
  }
  const schemas: Record<ActionName, z.ZodType> = ActionArgSchemas;
  const args = schemas[action].safeParse(argsJson.value);
  if (!args.success) {
    return { ok: false, code: "invalid_arguments", message: "Arguments do not match the action.", commandId: cmd.id };
  }

  // Step 7: every resource id must be configured on this device.
  const target = resolveTarget(action, args.data as Record<string, unknown>, ctx.config);
  if (target === null) {
    return {
      ok: false,
      code: "resource_not_approved",
      message: "The requested item is not approved on this device.",
      commandId: cmd.id,
    };
  }

  return {
    ok: true,
    command: {
      id: cmd.id,
      action,
      target,
      issuedAt: Date.parse(cmd.issued_at),
      expiresAt: Date.parse(cmd.expires_at),
    },
  };
}

/** Verifies a cancel frame. Cancels are signed like commands, with the reserved action name and empty arguments. */
export function verifyCancel(text: string, ctx: VerifyContext): CancelVerifyResult {
  const json = parseJson(text);
  if (!json.ok) {
    return { ok: false, reason: "not_json" };
  }
  const parsed = CancelSchema.safeParse(json.value);
  if (!parsed.success) {
    return { ok: false, reason: "malformed" };
  }
  const cancel = parsed.data;
  const signatureOk = verifyFields(
    ctx.secret,
    {
      id: cancel.command_id,
      action: CANCEL_ACTION,
      issued_at: cancel.issued_at,
      expires_at: cancel.expires_at,
      nonce: cancel.nonce,
      args_json: "{}",
    },
    cancel.signature,
  );
  if (!signatureOk) {
    return { ok: false, reason: "signature" };
  }
  if (checkWindow(cancel.issued_at, cancel.expires_at, ctx.nowMs) !== null) {
    return { ok: false, reason: "window" };
  }
  // A cancel is recorded under the command it names. Only its nonce is checked for replay, as before.
  const floorMs = ctx.replay.issuedFloorAt(ctx.nowMs);
  if ((floorMs !== null && Date.parse(cancel.issued_at) < floorMs) || ctx.replay.hasNonce(cancel.nonce, ctx.nowMs)) {
    return { ok: false, reason: "replayed" };
  }
  ctx.replay.remember({ id: cancel.command_id, nonce: cancel.nonce }, ctx.nowMs);
  return { ok: true, commandId: cancel.command_id };
}
