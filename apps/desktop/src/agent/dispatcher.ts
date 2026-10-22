/**
 * Dispatcher: runs verified commands through their action handler. Rules:
 * - one command at a time per action name; other actions run concurrently;
 * - cancel before the action has started (no pid, no OS request) stops it and reports cancelled;
 * - cancel after the action has started is refused, and the honest result says the process cannot be undone;
 * - every command yields exactly one result, delivered through onResult.
 */
import { PlatformError, type Platform } from "../platform/platform.js";
import type { AgentResult, AgentErrorCode, ActionName } from "../shared/protocol.js";
import type { VerifiedCommand } from "./command-verifier.js";
import { DEFAULT_HANDLERS, type ActionHandlers, type ActionInput, type ActionOutcome } from "./actions/index.js";
import { ActionError } from "./actions/types.js";
import { silentLogger, type Logger } from "./logger.js";

export const CANCELLED_MESSAGE = "Cancelled by the user before the action started.";

export type CancelOutcome =
  | { cancelled: true }
  | { cancelled: false; reason: "unknown_or_finished" | "already_started" };

export interface DispatcherOptions {
  platform: Platform;
  onResult: (result: AgentResult) => Promise<void>;
  handlers?: ActionHandlers;
  logger?: Logger;
  now?: () => Date;
}

type RunStatus = "queued" | "running" | "finished";

interface RunState {
  command: VerifiedCommand;
  status: RunStatus;
  controller: AbortController;
  /** True once the action has produced a pid or handed a request to the OS. */
  started: boolean;
  cancelRequested: boolean;
  startedAt: Date | null;
}

export class Dispatcher {
  private readonly platform: Platform;
  private readonly onResult: (result: AgentResult) => Promise<void>;
  private readonly handlers: ActionHandlers;
  private readonly logger: Logger;
  private readonly now: () => Date;
  private readonly runs = new Map<string, RunState>();
  private readonly chains = new Map<ActionName, Promise<void>>();

  constructor(options: DispatcherOptions) {
    this.platform = options.platform;
    this.onResult = options.onResult;
    this.handlers = options.handlers ?? DEFAULT_HANDLERS;
    this.logger = options.logger ?? silentLogger;
    this.now = options.now ?? (() => new Date());
  }

  /** Queues the command behind any earlier command of the same action. Returns false for a duplicate id. */
  submit(command: VerifiedCommand): boolean {
    if (this.runs.has(command.id)) {
      return false;
    }
    const state: RunState = {
      command,
      status: "queued",
      controller: new AbortController(),
      started: false,
      cancelRequested: false,
      startedAt: null,
    };
    this.runs.set(command.id, state);
    const previous = this.chains.get(command.action) ?? Promise.resolve();
    const next = previous
      .then(() => this.execute(state))
      .catch((error: unknown) => {
        this.logger.error("Command dispatch failed unexpectedly.", { action: command.action, errorName: errorName(error) });
      });
    this.chains.set(command.action, next);
    void next.then(() => {
      if (this.chains.get(command.action) === next) {
        this.chains.delete(command.action);
      }
    });
    return true;
  }

  cancel(commandId: string): CancelOutcome {
    const state = this.runs.get(commandId);
    if (state === undefined || state.status === "finished") {
      return { cancelled: false, reason: "unknown_or_finished" };
    }
    if (state.started) {
      state.cancelRequested = true;
      return { cancelled: false, reason: "already_started" };
    }
    state.controller.abort();
    return { cancelled: true };
  }

  /** Command ids that are executing right now (for the heartbeat). Queued commands are not included. */
  runningCommandIds(): string[] {
    return [...this.runs.values()].filter((run) => run.status === "running").map((run) => run.command.id);
  }

  /** Resolves when every queued and running command has delivered its result. */
  async idle(): Promise<void> {
    while (this.chains.size > 0) {
      await Promise.allSettled([...this.chains.values()]);
    }
  }

  private async execute(state: RunState): Promise<void> {
    const { command } = state;
    if (state.controller.signal.aborted) {
      await this.finish(state, this.cancelledResult(state, null));
      return;
    }
    state.status = "running";
    state.startedAt = this.now();

    let outcome: ActionOutcome;
    try {
      outcome = await this.invoke(state);
    } catch (error) {
      outcome = this.outcomeFromError(error, command.action);
    }

    if (state.controller.signal.aborted && !state.started) {
      // The action was cancelled before anything irreversible happened, whatever it returned.
      await this.finish(state, this.cancelledResult(state, state.startedAt));
      return;
    }
    await this.finish(state, this.toResult(state, outcome));
  }

  private invoke(state: RunState): Promise<ActionOutcome> {
    const input: ActionInput<ActionName> = {
      target: state.command.target,
      platform: this.platform,
      signal: state.controller.signal,
      markStarted: () => {
        state.started = true;
      },
    };
    const handler = this.handlers[state.command.action] as unknown as (
      value: ActionInput<ActionName>,
    ) => Promise<ActionOutcome>;
    return handler(input);
  }

  private outcomeFromError(error: unknown, action: ActionName): ActionOutcome {
    if (error instanceof PlatformError) {
      return failure(platformCode(error), error.message);
    }
    if (error instanceof ActionError) {
      return failure(error.code, error.message);
    }
    this.logger.error("Action handler failed.", { action, errorName: errorName(error) });
    return failure("agent_error", "The agent could not complete the action.");
  }

  private toResult(state: RunState, outcome: ActionOutcome): AgentResult {
    const startedAt = state.startedAt?.toISOString() ?? null;
    const finishedAt = this.now().toISOString();
    if (outcome.status === "succeeded") {
      let output = outcome.output;
      if (state.cancelRequested) {
        const summary = typeof output.summary === "string" ? output.summary : "";
        output = {
          ...output,
          summary: `${summary} Cancellation came after the action had started, and a started action cannot be undone.`.trim(),
        };
      }
      return {
        type: "result",
        command_id: state.command.id,
        status: "succeeded",
        output,
        error: null,
        started_at: startedAt,
        finished_at: finishedAt,
      };
    }
    if (outcome.status === "cancelled") {
      return this.cancelledResult(state, state.startedAt);
    }
    return {
      type: "result",
      command_id: state.command.id,
      status: "failed",
      output: outcome.output ?? {},
      error: { code: outcome.error.code, message: outcome.error.message, details: outcome.error.details },
      started_at: startedAt,
      finished_at: finishedAt,
    };
  }

  private cancelledResult(state: RunState, startedAt: Date | null): AgentResult {
    return {
      type: "result",
      command_id: state.command.id,
      status: "cancelled",
      output: {},
      error: { code: "cancelled_by_user", message: CANCELLED_MESSAGE, details: {} },
      started_at: startedAt?.toISOString() ?? null,
      finished_at: this.now().toISOString(),
    };
  }

  private async finish(state: RunState, result: AgentResult): Promise<void> {
    state.status = "finished";
    this.runs.delete(state.command.id);
    try {
      await this.onResult(result);
    } catch (error) {
      this.logger.error("Could not record a command result.", { errorName: errorName(error) });
    }
  }
}

function failure(code: AgentErrorCode, message: string): ActionOutcome {
  return { status: "failed", error: { code, message, details: {} } };
}

function platformCode(error: PlatformError): AgentErrorCode {
  switch (error.code) {
    case "not_installed":
      return "not_installed";
    case "permission_denied":
      return "permission_denied";
    case "cancelled":
      return "execution_failed";
    case "execution_failed":
      return "execution_failed";
    case "unsupported_platform":
      return "agent_error";
  }
}

function errorName(error: unknown): string {
  return error instanceof Error ? error.name : "unknown";
}
