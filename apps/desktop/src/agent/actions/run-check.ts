/**
 * run_check: runs one approved project check from agent.json. The backend sends only a check id, which is resolved
 * against the local config before this handler runs (command-verifier.ts). The command line, its arguments and the
 * folder all come from the local config and are never sent by the backend.
 *
 * A check is never marked as started: the agent can stop it by killing its process tree, so a cancel is honoured
 * until the result is sent. Output is captured by the platform, capped, and kept as text only.
 */
import { cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

export const CHECK_OUTPUT_LIMIT_BYTES = 4096;

export async function runCheck({ target, platform, signal }: ActionInput<"run_check">): Promise<ActionOutcome> {
  const { check } = target;
  // A start failure (PlatformError) propagates to the dispatcher, which reports it as not_installed or execution_failed.
  const capture = await platform.runCapture({
    command: check.command,
    args: check.args,
    cwd: check.cwd,
    timeoutMs: check.timeoutSeconds * 1000,
    signal,
    maxOutputBytes: CHECK_OUTPUT_LIMIT_BYTES,
  });

  if (capture.kind === "cancelled") {
    return cancelled();
  }
  const base = {
    check_id: check.id,
    label: check.label,
    output_tail: capture.output,
    truncated: capture.truncated,
    duration_ms: capture.durationMs,
  };

  if (capture.kind === "timed_out") {
    const message = withSimulatedPrefix(
      platform.simulated,
      `The check "${check.label}" did not finish within ${check.timeoutSeconds} seconds and was stopped.`,
    );
    return {
      status: "failed",
      error: { code: "timeout", message, details: {} },
      output: { ...base, exit_code: null },
    };
  }

  if (capture.exitCode === 0) {
    return succeeded({
      ...base,
      exit_code: 0,
      summary: withSimulatedPrefix(platform.simulated, `The check "${check.label}" passed (exit code 0).`),
    });
  }
  return {
    status: "failed",
    error: {
      code: "check_failed",
      message: withSimulatedPrefix(
        platform.simulated,
        `The check "${check.label}" failed with exit code ${capture.exitCode}.`,
      ),
      details: {},
    },
    output: { ...base, exit_code: capture.exitCode },
  };
}
