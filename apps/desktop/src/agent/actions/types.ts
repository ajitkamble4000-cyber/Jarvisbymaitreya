/**
 * Shared types for action handlers. A handler receives a resolved local target and a platform adapter. It throws
 * ActionError (or PlatformError) for failures; the dispatcher turns those into honest failed results.
 */
import type { Platform } from "../../platform/platform.js";
import type { ActionName, AgentErrorCode } from "../../shared/protocol.js";
import type { TargetOf } from "../command-verifier.js";

export type ActionFailure = {
  status: "failed";
  error: { code: AgentErrorCode; message: string; details: Record<string, unknown> };
  /** Optional result output sent with a failure (for example the captured output of a failed check). */
  output?: Record<string, unknown>;
};

export type ActionOutcome =
  | { status: "succeeded"; output: Record<string, unknown> }
  | ActionFailure
  | { status: "cancelled" };

export interface ActionInput<A extends ActionName> {
  target: TargetOf<A>;
  platform: Platform;
  signal: AbortSignal;
  /** Call once the action has done something that cannot be undone (a pid, or a request to the OS). */
  markStarted: () => void;
}

export type ActionHandler<A extends ActionName> = (input: ActionInput<A>) => Promise<ActionOutcome>;

export type ActionHandlers = { [A in ActionName]: ActionHandler<A> };

export class ActionError extends Error {
  readonly code: AgentErrorCode;

  constructor(code: AgentErrorCode, message: string) {
    super(message);
    this.name = "ActionError";
    this.code = code;
  }
}

export function succeeded(output: Record<string, unknown>): ActionOutcome {
  return { status: "succeeded", output };
}

export function cancelled(): ActionOutcome {
  return { status: "cancelled" };
}

/** Labels a result as simulated when the platform is simulated, so no simulated result can pass as real. */
export function withSimulatedPrefix(simulated: boolean, summary: string): string {
  return simulated ? `SIMULATED: ${summary}` : summary;
}
