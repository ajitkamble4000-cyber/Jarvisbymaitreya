import { describe, expect, it } from "vitest";
import {
  CLOCK_SKEW_MS,
  MAX_COMMAND_LIFETIME_MS,
  REPLAY_WINDOW_MS,
  ReplayCache,
  verifyCancel,
  verifyCommand,
  type VerifyContext,
} from "../src/agent/command-verifier.js";
import { defaultConfig } from "../src/agent/config.js";
import { fixtureConfig, signedCancelText, signedCommandText, TEST_SECRET } from "./helpers.js";

const NOW = Date.parse("2026-10-19T04:51:40Z");

function context(overrides: Partial<VerifyContext> = {}): VerifyContext {
  return {
    secret: TEST_SECRET,
    config: fixtureConfig(),
    nowMs: NOW,
    replay: new ReplayCache(),
    ...overrides,
  };
}

function expectRejected(result: ReturnType<typeof verifyCommand>, code: string) {
  expect(result.ok).toBe(false);
  if (result.ok) {
    throw new Error("expected rejection");
  }
  expect(result.code).toBe(code);
  return result;
}

describe("verifyCommand: accepted commands", () => {
  it("accepts a signed launch_app for an approved app and resolves the local entry", () => {
    const result = verifyCommand(signedCommandText({ args: { app_id: "chrome" } }, NOW), context());
    expect(result.ok).toBe(true);
    if (!result.ok) {
      return;
    }
    expect(result.command.action).toBe("launch_app");
    expect(result.command.target).toMatchObject({ action: "launch_app", app: { id: "chrome", label: "Google Chrome" } });
  });

  it("resolves open_url by id and by exact URL", () => {
    const byId = verifyCommand(signedCommandText({ action: "open_url", args: { url_id: "youtube" } }, NOW), context());
    const byUrl = verifyCommand(
      signedCommandText({ action: "open_url", args: { url: "https://www.youtube.com" } }, NOW),
      context(),
    );
    expect(byId.ok && byId.command.target).toMatchObject({ action: "open_url", url: { url: "https://www.youtube.com" } });
    expect(byUrl.ok && byUrl.command.target).toMatchObject({ action: "open_url", url: { id: "youtube" } });
  });

  it("uses the project's app when open_project names none, and vscode otherwise", () => {
    const config = fixtureConfig();
    const result = verifyCommand(signedCommandText({ action: "open_project", args: { project_id: "jarvis" } }, NOW), context({ config }));
    expect(result.ok && result.command.target).toMatchObject({ action: "open_project", app: { id: "vscode" } });
  });
});

describe("verifyCommand: the seven steps of protocol section 4", () => {
  it("step 1: rejects a frame that is not JSON", () => {
    const result = expectRejected(verifyCommand("not json", context()), "invalid_arguments");
    expect(result.commandId).toBeNull();
  });

  it("step 1: rejects a protocol version other than 1", () => {
    expectRejected(verifyCommand(signedCommandText({ protocolVersion: 2 }, NOW), context()), "invalid_arguments");
  });

  it("step 2: rejects tampered arguments and does not say why", () => {
    const parsed = JSON.parse(signedCommandText({ args: { app_id: "chrome" } }, NOW)) as Record<string, unknown>;
    parsed.args_json = '{"app_id":"vscode"}';
    const result = expectRejected(verifyCommand(JSON.stringify(parsed), context()), "signature_invalid");
    expect(result.message).not.toMatch(/signature/i);
    // The claimed id of an unverified frame is not echoed back.
    expect(result.commandId).toBeNull();
  });

  it("step 2: rejects a command signed with a different secret", () => {
    const text = signedCommandText({ secret: "someone-else" }, NOW);
    expectRejected(verifyCommand(text, context()), "signature_invalid");
  });

  it("step 3: rejects an expired command", () => {
    const text = signedCommandText({ issuedAtMs: NOW - 120_000, expiresAtMs: NOW - 1 }, NOW);
    expectRejected(verifyCommand(text, context()), "expired");
  });

  it("step 3: rejects a command issued more than 30 seconds in the future", () => {
    const text = signedCommandText({ issuedAtMs: NOW + 60_000, expiresAtMs: NOW + 120_000 }, NOW);
    expectRejected(verifyCommand(text, context()), "invalid_arguments");
  });

  it("step 3: accepts a command issued 25 seconds in the future (clock skew)", () => {
    const text = signedCommandText({ issuedAtMs: NOW + 25_000, expiresAtMs: NOW + 120_000 }, NOW);
    expect(verifyCommand(text, context()).ok).toBe(true);
  });

  it("step 4: rejects a replayed command id", () => {
    const replay = new ReplayCache();
    const text = signedCommandText({ id: "fixed-id-0001", nonce: "nonce-a" }, NOW);
    expect(verifyCommand(text, context({ replay })).ok).toBe(true);
    expectRejected(verifyCommand(text, context({ replay, nowMs: NOW + 1000 })), "replayed");
  });

  it("step 4: rejects a replayed nonce under a new id", () => {
    const replay = new ReplayCache();
    const first = signedCommandText({ id: "fixed-id-0002", nonce: "shared-nonce" }, NOW);
    const second = signedCommandText({ id: "fixed-id-0003", nonce: "shared-nonce" }, NOW);
    expect(verifyCommand(first, context({ replay })).ok).toBe(true);
    expectRejected(verifyCommand(second, context({ replay })), "replayed");
  });

  it("step 4: a replay entry is forgotten only after the command itself has expired", () => {
    const replay = new ReplayCache();
    const text = signedCommandText({ id: "fixed-id-0004", nonce: "nonce-old", expiresAtMs: NOW + 9 * 60 * 1000 }, NOW);
    expect(verifyCommand(text, context({ replay })).ok).toBe(true);
    // Replay after the cache has forgotten the command: the frame is expired, so it is refused.
    const later = NOW + REPLAY_WINDOW_MS + 1;
    expectRejected(verifyCommand(text, context({ replay, nowMs: later })), "expired");
  });

  it("step 3: refuses a command whose lifetime is longer than the replay cache can cover", () => {
    const text = signedCommandText({ expiresAtMs: NOW + MAX_COMMAND_LIFETIME_MS + 60_000 }, NOW);
    expectRejected(verifyCommand(text, context()), "invalid_arguments");
  });

  it("caps the lifetime at 540 seconds, the backend TTL limit", () => {
    expect(MAX_COMMAND_LIFETIME_MS).toBe(540_000);
    const atCap = signedCommandText({ expiresAtMs: NOW + 540_000 }, NOW);
    expect(verifyCommand(atCap, context()).ok).toBe(true);
  });

  it("step 3: refuses a command whose expires_at - issued_at is 1 ms over 540 seconds", () => {
    const text = signedCommandText({ issuedAtMs: NOW, expiresAtMs: NOW + 540_001 }, NOW);
    expectRejected(verifyCommand(text, context()), "invalid_arguments");
  });

  it("keeps every accepted command inside the replay window for its whole validity", () => {
    // A command accepted at NOW can be issued up to 30 s ahead and lives 540 s, so it ends by NOW + 570 s.
    // The record made at NOW lasts REPLAY_WINDOW_MS, which must reach past that point.
    expect(MAX_COMMAND_LIFETIME_MS + CLOCK_SKEW_MS).toBeLessThan(REPLAY_WINDOW_MS);
  });

  it("step 4: records a command that a later step refuses, so the refusal cannot be undone by a replay", () => {
    const replay = new ReplayCache();
    const config = fixtureConfig();
    config.capabilities.launch_app = false;
    const text = signedCommandText({ id: "refused-then-replayed", args: { app_id: "chrome" }, nonce: "refused-nonce" }, NOW);
    expectRejected(verifyCommand(text, context({ config, replay })), "permission_denied");
    expect(replay.hasId("refused-then-replayed", NOW)).toBe(true);
    expect(replay.hasNonce("refused-nonce", NOW)).toBe(true);
    expectRejected(verifyCommand(text, context({ config: fixtureConfig(), replay })), "replayed");
  });

  it("step 4: records an accepted command under its id and nonce", () => {
    const replay = new ReplayCache();
    const text = signedCommandText({ id: "recorded-1", nonce: "recorded-nonce" }, NOW);
    expect(verifyCommand(text, context({ replay })).ok).toBe(true);
    expect(replay.hasId("recorded-1", NOW)).toBe(true);
    expect(replay.hasNonce("recorded-nonce", NOW)).toBe(true);
    expect(replay.revision).toBe(1);
  });

  it("step 4 (degraded store): refuses a command issued more than 60 s before start-up", () => {
    const startedAt = NOW;
    const replay = new ReplayCache({ issuedFloorMs: startedAt - 60_000 });
    const old = signedCommandText({ issuedAtMs: startedAt - 61_000, expiresAtMs: startedAt + 60_000 }, NOW);
    const result = expectRejected(verifyCommand(old, context({ replay })), "replayed");
    expect(result.message).toMatch(/before replay protection could be verified/);
    expect(replay.size()).toBe(0);
  });

  it("step 4 (degraded store): accepts a command issued 59 s before start-up, and exactly 60 s before", () => {
    const startedAt = NOW;
    const replay = new ReplayCache({ issuedFloorMs: startedAt - 60_000 });
    const recent = signedCommandText({ id: "recent-1", nonce: "recent-n1", issuedAtMs: startedAt - 59_000, expiresAtMs: startedAt + 60_000 }, NOW);
    const boundary = signedCommandText({ id: "edge-1", nonce: "edge-n1", issuedAtMs: startedAt - 60_000, expiresAtMs: startedAt + 60_000 }, NOW);
    expect(verifyCommand(recent, context({ replay })).ok).toBe(true);
    expect(verifyCommand(boundary, context({ replay })).ok).toBe(true);
  });

  it("step 4 (degraded store): a command issued after the floor is still checked for replay", () => {
    const startedAt = NOW;
    const replay = new ReplayCache({ issuedFloorMs: startedAt - 60_000 });
    const text = signedCommandText({ id: "floor-replay-1", nonce: "floor-replay-n", issuedAtMs: startedAt - 10_000, expiresAtMs: startedAt + 60_000 }, NOW);
    expect(verifyCommand(text, context({ replay })).ok).toBe(true);
    expectRejected(verifyCommand(text, context({ replay })), "replayed");
  });

  it("step 5: rejects an action that is not supported", () => {
    const text = signedCommandText({ action: "shutdown_pc", args: {} }, NOW);
    expectRejected(verifyCommand(text, context()), "unsupported_action");
  });

  it("step 5: rejects a supported action whose capability is disabled", () => {
    const config = fixtureConfig();
    config.capabilities.launch_app = false;
    const text = signedCommandText({ args: { app_id: "chrome" } }, NOW);
    expectRejected(verifyCommand(text, context({ config })), "permission_denied");
  });

  it("step 6: rejects an extra key in the arguments", () => {
    const text = signedCommandText({ args: { app_id: "chrome", command: "cmd.exe" } }, NOW);
    expectRejected(verifyCommand(text, context()), "invalid_arguments");
  });

  it("step 6: rejects arguments that are not an object", () => {
    const text = signedCommandText({ argsJson: '["chrome"]' }, NOW);
    expectRejected(verifyCommand(text, context()), "invalid_arguments");
  });

  it("step 6: rejects an id with characters outside the resource id rule", () => {
    const text = signedCommandText({ args: { app_id: "Chrome Browser" } }, NOW);
    expectRejected(verifyCommand(text, context()), "invalid_arguments");
  });

  it("step 7: rejects an unknown resource id", () => {
    const text = signedCommandText({ args: { app_id: "firefox" } }, NOW);
    expectRejected(verifyCommand(text, context()), "resource_not_approved");
  });

  it("step 7: rejects a URL that is not an exact approved entry", () => {
    const text = signedCommandText({ action: "open_url", args: { url: "https://www.youtube.com/watch?v=x" } }, NOW);
    expectRejected(verifyCommand(text, context()), "resource_not_approved");
  });

  it("step 7: rejects open_project with an unapproved app override", () => {
    const text = signedCommandText({ action: "open_project", args: { project_id: "jarvis", app_id: "chrome" } }, NOW);
    expect(verifyCommand(text, context()).ok).toBe(true);
    const unknown = signedCommandText({ action: "open_project", args: { project_id: "jarvis", app_id: "notepad" } }, NOW);
    expectRejected(verifyCommand(unknown, context()), "resource_not_approved");
  });

  it("step 7: refuses an approved id when the config has no such entry", () => {
    const config = defaultConfig();
    const text = signedCommandText({ action: "read_file", args: { file_id: "notes" } }, NOW);
    expectRejected(verifyCommand(text, context({ config })), "resource_not_approved");
  });
});

describe("verifyCancel", () => {
  it("accepts a correctly signed cancel", () => {
    const result = verifyCancel(signedCancelText("cmd-x", NOW), context());
    expect(result).toEqual({ ok: true, commandId: "cmd-x" });
  });

  it("rejects a cancel with a bad signature", () => {
    const text = signedCancelText("cmd-y", NOW, { secret: "other" });
    expect(verifyCancel(text, context())).toMatchObject({ ok: false, reason: "signature" });
  });

  it("rejects a replayed cancel", () => {
    const replay = new ReplayCache();
    const text = signedCancelText("cmd-z", NOW, { nonce: "cancel-once" });
    expect(verifyCancel(text, context({ replay })).ok).toBe(true);
    expect(verifyCancel(text, context({ replay })).ok).toBe(false);
  });

  it("refuses a cancel issued before the degraded-store floor", () => {
    // The cancel is issued 30 s ago and still valid; the floor sits 20 s ago, so the cancel is older than the floor.
    const replay = new ReplayCache({ issuedFloorMs: NOW - 20_000 });
    const text = signedCancelText("cmd-old", NOW - 30_000, { nonce: "cancel-old" });
    expect(verifyCancel(text, context({ replay }))).toMatchObject({ ok: false, reason: "replayed" });
    expect(verifyCancel(signedCancelText("cmd-new", NOW, { nonce: "cancel-new" }), context({ replay })).ok).toBe(true);
  });
});

describe("ReplayCache", () => {
  it("evicts the oldest entries beyond its limit", () => {
    const cache = new ReplayCache({ windowMs: 60_000, limit: 2 });
    cache.remember({ id: "a", nonce: "na" }, NOW);
    cache.remember({ id: "b", nonce: "nb" }, NOW);
    cache.remember({ id: "c", nonce: "nc" }, NOW);
    expect(cache.size()).toBe(2);
    expect(cache.hasId("a", NOW)).toBe(false);
    expect(cache.hasNonce("na", NOW)).toBe(false);
    expect(cache.hasId("c", NOW)).toBe(true);
  });

  it("forgets a record once its window has passed", () => {
    const cache = new ReplayCache({ windowMs: 60_000 });
    cache.remember({ id: "x", nonce: "nx" }, NOW);
    expect(cache.hasId("x", NOW + 59_999)).toBe(true);
    expect(cache.hasId("x", NOW + 60_000)).toBe(false);
  });

  it("snapshots live records oldest first and drops expired ones", () => {
    const cache = new ReplayCache({ windowMs: 60_000 });
    cache.remember({ id: "old", nonce: "n-old" }, NOW);
    cache.remember({ id: "new", nonce: "n-new" }, NOW + 30_000);
    expect(cache.snapshot(NOW + 60_000)).toEqual([{ id: "new", nonce: "n-new", seen_at: NOW + 30_000 }]);
  });
});
