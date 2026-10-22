import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { StatusDot } from "../../components/ui/StatusDot";
import { Card } from "../../components/ui/Card";
import { Link } from "react-router-dom";
import { useTaskAction, useTaskQuery } from "../../services/queries";
import type { TaskDecision, TaskOut } from "../../types/api";
import { describeTask, statusTone, taskActionStates, TASK_STATUS_LABEL } from "../tasks/taskModel";

export function decisionText(decision: TaskDecision | undefined, reason: string | undefined): string | null {
  switch (decision) {
    case "awaiting_approval":
      return "This needs your approval before it runs.";
    case "queued":
      return "Queued. It runs when the device is online and the policy allows it.";
    case "needs_device_selection":
      return "More than one device matches. Choose one in Tasks before it can run.";
    case "rejected":
      return reason ? `Not allowed: ${reason}` : "Not allowed by the current policy.";
    default:
      return null;
  }
}

export interface ToolProposalCardProps {
  task: TaskOut;
  decision?: TaskDecision;
  reason?: string;
}

/**
 * A proposed task inside the chat, with Approve and Deny wired to the tasks API. The task passed in is a
 * snapshot from when the proposal arrived. After this card's own action succeeds, the card shows the task
 * the server returned, so it never offers an approval that was already given.
 */
export function ToolProposalCard({ task: snapshot, decision, reason }: ToolProposalCardProps) {
  const action = useTaskAction();
  const pending = action.isPending ? (action.variables?.action ?? null) : null;
  const updated = action.isSuccess && action.data?.id === snapshot.id ? action.data : null;
  const task = updated ?? snapshot;
  const states = taskActionStates(task, pending);
  const note = updated ? null : decisionText(decision, reason);

  return (
    <Card className="flex flex-col gap-3 p-4" aria-label={`Task: ${task.title}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold text-ink">{task.title}</p>
        <StatusDot tone={statusTone(task.status)} label={TASK_STATUS_LABEL[task.status]} className="text-xs" />
      </div>
      <div className="flex flex-wrap gap-2 text-xs text-muted">
        <Badge>{task.tool_name}</Badge>
        {task.device ? <Badge>{task.device.name}</Badge> : null}
        <Badge>{task.risk_level} risk</Badge>
      </div>
      <p role="status" aria-live="polite" className="text-sm text-muted">
        {note ?? describeTask(task)}
      </p>
      {action.isError && action.variables?.id === task.id ? (
        <p role="alert" className="text-xs font-medium text-danger">
          {action.error.message}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="primary" disabled={!states.approve.enabled} loading={pending === "approve"} onClick={() => action.mutate({ id: task.id, action: "approve" })}>
          Approve
        </Button>
        <Button size="sm" variant="danger" disabled={!states.deny.enabled} loading={pending === "deny"} onClick={() => action.mutate({ id: task.id, action: "deny" })}>
          Deny
        </Button>
        <Link to={`/tasks/${task.id}`} className="self-center text-xs text-accent underline underline-offset-4">
          View in Tasks
        </Link>
      </div>
    </Card>
  );
}

/** Loads a task that a stored message refers to, then shows its card. */
export function TaskProposalById({ taskId }: { taskId: string }) {
  const detail = useTaskQuery(taskId);
  if (detail.isPending) return <SkeletonList rows={1} label="Loading task" />;
  if (detail.isError) return <ErrorState message={detail.error.message} onRetry={() => void detail.refetch()} />;
  return <ToolProposalCard task={detail.data.task} />;
}
