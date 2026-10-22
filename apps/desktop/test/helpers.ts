/**
 * Shared test fixtures. Signing here uses the same canonical helper the agent uses; the parity test separately pins
 * the bytes against the Python backend.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { signFields } from "../src/shared/canonical.js";
import { PROTOCOL_VERSION } from "../src/shared/protocol.js";
import { defaultConfig, type AgentConfig } from "../src/agent/config.js";

export const TEST_SECRET = "test-device-secret-0123456789abcdef";
export const SERVER_PAIR_PATH = "/api/v1/agent/pair";
export const WS_PATH = "/api/v1/agent/ws";

export function tempDir(prefix = "jarvis-agent-test-"): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

export interface SignedCommandOptions {
  id?: string;
  action?: string;
  args?: unknown;
  argsJson?: string;
  nonce?: string;
  issuedAtMs?: number;
  expiresAtMs?: number;
  secret?: string;
  protocolVersion?: number;
  signatureOverride?: string;
}

let counter = 0;

/** Builds a command frame exactly as the backend does: signed over the six fields. */
export function signedCommandText(options: SignedCommandOptions, nowMs: number): string {
  counter += 1;
  const id = options.id ?? `cmd-${counter.toString().padStart(6, "0")}-0000-4000-8000-000000000000`;
  const action = options.action ?? "launch_app";
  const argsJson = options.argsJson ?? JSON.stringify(options.args ?? { app_id: "chrome" });
  const nonce = options.nonce ?? `nonce-${counter}-${nowMs}`;
  const issued = new Date(options.issuedAtMs ?? nowMs).toISOString().replace(".000Z", "Z");
  const expires = new Date(options.expiresAtMs ?? nowMs + 60_000).toISOString().replace(".000Z", "Z");
  const signature =
    options.signatureOverride ??
    signFields(options.secret ?? TEST_SECRET, {
      id,
      action,
      issued_at: issued,
      expires_at: expires,
      nonce,
      args_json: argsJson,
    });
  return JSON.stringify({
    type: "command",
    id,
    action,
    args_json: argsJson,
    issued_at: issued,
    expires_at: expires,
    nonce,
    signature,
    protocol_version: options.protocolVersion ?? PROTOCOL_VERSION,
  });
}

export function signedCancelText(commandId: string, nowMs: number, options: { secret?: string; nonce?: string } = {}): string {
  counter += 1;
  const issued = new Date(nowMs).toISOString().replace(".000Z", "Z");
  const expires = new Date(nowMs + 60_000).toISOString().replace(".000Z", "Z");
  const nonce = options.nonce ?? `cancel-nonce-${counter}-${nowMs}`;
  const signature = signFields(options.secret ?? TEST_SECRET, {
    id: commandId,
    action: "__cancel__",
    issued_at: issued,
    expires_at: expires,
    nonce,
    args_json: "{}",
  });
  return JSON.stringify({ type: "cancel", command_id: commandId, issued_at: issued, expires_at: expires, nonce, signature });
}

/** A config with one of each resource kind. Paths are fake but absolute for the local (Linux) run. */
export function fixtureConfig(): AgentConfig {
  const config = defaultConfig();
  return {
    ...config,
    approvedUrls: [{ id: "youtube", label: "YouTube", url: "https://www.youtube.com" }],
    approvedFolders: [{ id: "downloads", label: "Downloads", path: path.resolve("/fixture/Downloads") }],
    approvedProjects: [{ id: "jarvis", label: "JARVIS project", path: path.resolve("/fixture/jarvis") }],
    approvedFiles: [{ id: "notes", label: "Meeting notes", path: path.resolve("/fixture/notes.txt") }],
  };
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function waitFor(predicate: () => boolean, timeoutMs = 5_000, stepMs = 10): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) {
      throw new Error("waitFor timed out");
    }
    await sleep(stepMs);
  }
}

export function removeDir(dir: string): void {
  fs.rmSync(dir, { recursive: true, force: true });
}
