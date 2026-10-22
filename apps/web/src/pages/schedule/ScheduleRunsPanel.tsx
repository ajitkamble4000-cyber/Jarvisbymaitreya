import { Link } from "react-router-dom";
import { Badge } from "../../components/ui/Badge";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { formatDateTime, humanize } from "../../services/format";
import { useScheduleRuns } from "./scheduleQueries";
import { runErrorText } from "./scheduleModel";

export interface ScheduleRunsPanelProps {
  scheduleId: string;
}

/** Run history for one schedule, newest first as the API returns it. */
export function ScheduleRunsPanel({ scheduleId }: ScheduleRunsPanelProps) {
  const runs = useScheduleRuns(scheduleId, true);

  if (runs.isPending) return <SkeletonList rows={2} label="Loading run history" />;
  if (runs.isError) return <ErrorState message={runs.error.message} onRetry={() => void runs.refetch()} />;

  const items = runs.data ?? [];
  if (items.length === 0) return <p className="text-sm text-muted">No runs yet.</p>;

  return (
    <ol aria-label="Run history" className="flex flex-col gap-2">
      {items.map((run) => {
        const errorText = runErrorText(run.error);
        return (
          <li key={run.id} className="flex flex-col gap-1 rounded-lg border border-line bg-raised p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={run.status === "failed" ? "danger" : run.status === "completed" ? "success" : "neutral"}>{humanize(run.status)}</Badge>
              <span className="text-muted">Scheduled for {formatDateTime(run.scheduled_for)}</span>
              <span className="text-muted">Attempt {run.attempt}</span>
            </div>
            {run.finished_at ? <span className="text-xs text-muted">Finished {formatDateTime(run.finished_at)}</span> : null}
            {errorText ? <p className="text-xs text-danger">{errorText}</p> : null}
            {run.task_id ? (
              <Link to={`/tasks/${run.task_id}`} className="w-fit text-xs text-accent underline underline-offset-4">
                Open the task for this run
              </Link>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
