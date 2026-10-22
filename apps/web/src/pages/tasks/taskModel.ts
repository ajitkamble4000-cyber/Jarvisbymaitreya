import type { TaskCounts, TaskOut, TaskStatus } from "../../types/api";
import type { TaskListParams } from "../../services/queries";
import type { StatusTone } from "../../components/ui/StatusDot";

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_approval: "Awaiting approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export type TaskFilterId = "all" | TaskStatus;

export interface TaskFilter {
  id: TaskFilterId;
  label: string;
}

export const TASK_FILTERS: readonly TaskFilter[] = [
  { id: "all", label: "All" },
  { id: "awaiting_approval", label: "Awaiting approval" },
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "completed", label: "Completed" },
  { id: "failed", label: "Failed" },
  { id: "cancelled", label: "Cancelled" },
];

/** Reads a filter from the URL. Unknown or missing values fall back to All. */
export function taskFilterFromParam(value: string | null): TaskFilterId {
  return TASK_FILTERS.some((filter) => filter.id === value) ? (value as TaskFilterId) : "all";
}

/** The count shown on a filter tab. "All" is the sum of every status. */
export function countForFilter(counts: TaskCounts | undefined, filter: TaskFilterId): number {
  if (!counts) return 0;
  if (filter === "all") return totalCount(counts);
  return counts[filter];
}

export function totalCount(counts: TaskCounts): number {
  return counts.queued + counts.running + counts.awaiting_approval + counts.completed + counts.failed + counts.cancelled;
}

/** Builds the list query for a filter tab and a search string. Pure. */
export function buildTaskQuery(filter: TaskFilterId, search: string, limit = 50): TaskListParams {
  const q = search.trim();
  return {
    status: filter,
    ...(q ? { q } : {}),
    limit,
  };
}

/** A finished task can be run again as a new task. A running or waiting one cannot, so there is no second copy of it. */
export function canRunAgain(status: TaskStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export function statusTone(status: TaskStatus): StatusTone {
  switch (status) {
    case "awaiting_approval":
      return "warning";
    case "running":
    case "queued":
      return "info";
    case "completed":
      return "success";
    case "failed":
      return "danger";
    case "cancelled":
    default:
      return "muted";
  }
}

/**
 * True when a completed task did not confirm its effect: the desktop could not verify the action
 * (output.verified is false), or a cancel was requested and the target is still running. Pure.
 */
export function isUnconfirmedResult(task: TaskOut): boolean {
  const output = task.result?.output;
  if (typeof output !== "object" || output === null) return false;
  const record = output as Record<string, unknown>;
  if (record.verified === false) return true;
  return task.tool_name === "cancel_task" && record.status === "running";
}

/**
 * Plain-language description of where a task is. A queued task says it waits for the device,
 * because the policy holds it until the device is online. Pure.
 */
export function describeTask(task: TaskOut): string {
  if (task.status === "queued" && task.current_step === "waiting_for_device") {
    const device = task.device?.name ?? "the device";
    return `Waiting for ${device} to come online. Nothing has run yet.`;
  }
  if (task.status === "queued") return "Queued. Nothing has run yet.";
  if (task.status === "awaiting_approval") return task.current_step ?? "Waiting for your approval. Nothing runs until you approve.";
  if (task.status === "running") return task.current_step ?? "Running on the device.";
  if (task.status === "completed") {
    const summary = task.result?.summary ?? "Completed.";
    return isUnconfirmedResult(task) ? `Not confirmed: ${summary}` : summary;
  }
  // The error message is written for the user. The internal code is not shown in the sentence.
  if (task.status === "failed") return task.error?.message ?? "Failed.";
  return task.error?.message ?? "Cancelled.";
}

export interface ActionState {
  enabled: boolean;
}

export interface TaskActionStates {
  approve: ActionState;
  deny: ActionState;
  cancel: ActionState;
  retry: ActionState;
}

/**
 * Each button is enabled only when the API says the action is allowed (task.actions) and no
 * action is already running for this task. Pure.
 */
export function taskActionStates(task: TaskOut, pendingAction: string | null): TaskActionStates {
  const busy = pendingAction !== null;
  return {
    approve: { enabled: task.actions.approve && !busy },
    deny: { enabled: task.actions.deny && !busy },
    cancel: { enabled: task.actions.cancel && !busy },
    retry: { enabled: task.actions.retry && !busy },
  };
}
