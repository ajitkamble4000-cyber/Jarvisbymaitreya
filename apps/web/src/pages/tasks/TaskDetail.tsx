import { useNavigate } from "react-router-dom";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { StatusDot } from "../../components/ui/StatusDot";
import { formatDateTime, humanize } from "../../services/format";
import { useTaskAction, useTaskQuery, useCreateTask, type TaskActionName } from "../../services/queries";
import { describeTask, canRunAgain, isUnconfirmedResult, statusTone, taskActionStates, TASK_STATUS_LABEL } from "./taskModel";

export interface TaskDetailProps {
  taskId: string;
  onClose?: () => void;
}

/** Full view of one task: status, what it does, its real result or error, the timeline and actions. */
export function TaskDetail({ taskId, onClose }: TaskDetailProps) {
  const detail = useTaskQuery(taskId);
  const action = useTaskAction();
  const createTask = useCreateTask();
  const navigate = useNavigate();
  const pendingAction = action.isPending ? (action.variables?.action ?? null) : null;

  if (detail.isPending) return <SkeletonList rows={4} label="Loading task" />;
  if (detail.isError) {
    return <ErrorState message={detail.error.message} onRetry={() => void detail.refetch()} />;
  }

  const task = detail.data.task;
  const states = taskActionStates(task, pendingAction);

  // Run again makes a new task with the same tool, arguments and device. Policy applies to it as to any new task,
  // so an action that needs approval asks again.
  const runAgain = (): void => {
    createTask.mutate(
      {
        tool_name: task.tool_name,
        arguments: task.arguments,
        ...(task.device ? { device_id: task.device.id } : {}),
        title: task.title,
        description: task.description,
      },
      { onSuccess: (created) => navigate(`/tasks/${created.id}`) },
    );
  };

  const run = (name: TaskActionName): void => {
    action.mutate({ id: task.id, action: name });
  };

  return (
    <article aria-labelledby={`task-title-${task.id}`} className="flex flex-col gap-5">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusDot tone={statusTone(task.status)} label={TASK_STATUS_LABEL[task.status]} />
          <Badge>{humanize(task.risk_level)} risk</Badge>
          {task.device ? <Badge>{task.device.name}</Badge> : null}
        </div>
        <h2 id={`task-title-${task.id}`} className="text-lg font-semibold text-ink">
          {task.title}
        </h2>
        <p role="status" aria-live="polite" className="text-sm text-muted">
          {describeTask(task)}
        </p>
      </header>

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-muted">Tool</dt>
          <dd className="font-mono text-ink">{task.tool_name}</dd>
        </div>
        <div>
          <dt className="text-muted">Created</dt>
          <dd className="text-ink">{formatDateTime(task.created_at)}</dd>
        </div>
        <div>
          <dt className="text-muted">Finished</dt>
          <dd className="text-ink">{formatDateTime(task.finished_at)}</dd>
        </div>
        <div>
          <dt className="text-muted">Attempt</dt>
          <dd className="text-ink">
            {task.attempt} of {task.max_attempts}
          </dd>
        </div>
      </dl>

      {task.error ? (
        <div className="rounded-lg border border-danger/50 bg-danger/10 p-3 text-sm">
          <p className="font-medium text-danger">Error {task.error.code}</p>
          <p className="text-ink">{task.error.message}</p>
          {typeof task.error.details?.note === "string" && task.error.details.note ? (
            <p className="mt-1 text-muted">{task.error.details.note}</p>
          ) : null}
        </div>
      ) : null}

      {task.result ? (
        isUnconfirmedResult(task) ? (
          <div className="rounded-lg border border-warning/50 bg-warning/10 p-3 text-sm">
            <p className="font-medium text-warning">Not confirmed</p>
            <p className="text-ink">{task.result.summary}</p>
          </div>
        ) : (
          <div className="rounded-lg border border-success/40 bg-success/10 p-3 text-sm">
            <p className="font-medium text-success">Result</p>
            <p className="text-ink">{task.result.summary}</p>
          </div>
        )
      ) : null}

      <div className="flex flex-wrap gap-2" aria-label="Task actions">
        <Button variant="primary" size="sm" disabled={!states.approve.enabled} loading={pendingAction === "approve"} onClick={() => run("approve")}>
          Approve
        </Button>
        <Button variant="danger" size="sm" disabled={!states.deny.enabled} loading={pendingAction === "deny"} onClick={() => run("deny")}>
          Deny
        </Button>
        <Button variant="secondary" size="sm" disabled={!states.cancel.enabled} loading={pendingAction === "cancel"} onClick={() => run("cancel")}>
          Cancel task
        </Button>
        <Button variant="secondary" size="sm" disabled={!states.retry.enabled} loading={pendingAction === "retry"} onClick={() => run("retry")}>
          Retry
        </Button>
        {canRunAgain(task.status) ? (
          <Button variant="secondary" size="sm" loading={createTask.isPending} disabled={createTask.isPending} onClick={runAgain}>
            Run again
          </Button>
        ) : null}
        {onClose ? (
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        ) : null}
      </div>
      {action.isError ? <p role="alert" className="text-sm font-medium text-danger">{action.error.message}</p> : null}
      {createTask.isError ? <p role="alert" className="text-sm font-medium text-danger">{createTask.error.message}</p> : null}

      <section aria-labelledby={`timeline-${task.id}`} className="flex flex-col gap-2">
        <h3 id={`timeline-${task.id}`} className="text-sm font-semibold text-ink">
          Timeline
        </h3>
        {detail.data.events.length === 0 ? (
          <p className="text-sm text-muted">No events recorded yet.</p>
        ) : (
          <ol className="flex flex-col gap-2 border-l border-line pl-4">
            {detail.data.events.map((event) => (
              <li key={event.id} className="text-sm">
                <p className="text-ink">{event.message || humanize(event.type)}</p>
                <p className="text-xs text-muted">
                  <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
                </p>
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}

