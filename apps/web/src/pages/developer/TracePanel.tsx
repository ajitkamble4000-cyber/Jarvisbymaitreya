import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { ApiError } from "../../services/api";
import { formatDateTime, humanize } from "../../services/format";
import { useTraceQuery } from "./developerQueries";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Looks up one task by id and shows its status and event timeline. */
export function TracePanel() {
  const [draft, setDraft] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);
  const trace = useTraceQuery(taskId);

  const lookup = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const id = draft.trim();
    if (!UUID.test(id)) {
      setInputError("Enter a task id, which is a UUID such as 3f2a…");
      return;
    }
    setInputError(null);
    setTaskId(id);
  };

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={lookup} noValidate className="flex flex-wrap items-end gap-3" aria-label="Task trace lookup">
        <div className="min-w-0 flex-1">
          <Field label="Task id" error={inputError}>
            {(control) => (
              <Input {...control} invalid={Boolean(inputError)} autoComplete="off" value={draft} onChange={(event) => setDraft(event.target.value)} />
            )}
          </Field>
        </div>
        <Button type="submit" variant="secondary" loading={trace.isFetching && taskId !== null}>
          Look up trace
        </Button>
      </form>

      {taskId === null ? (
        <p className="text-sm text-muted">Enter a task id to see its status and events.</p>
      ) : trace.isPending ? (
        <SkeletonList rows={3} label="Loading trace" />
      ) : trace.isError ? (
        <ErrorState
          title="Trace not available"
          message={trace.error instanceof ApiError && trace.error.status === 404 ? "No task with this id belongs to this account." : trace.error.message}
        />
      ) : trace.data ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-ink">{trace.data.task.title}</span>
            <Badge tone="accent">{humanize(trace.data.task.status)}</Badge>
            <Link to={`/tasks/${trace.data.task.id}`} className="text-accent underline underline-offset-4">
              Open task
            </Link>
          </div>
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted">Tool</dt>
              <dd className="font-mono text-xs text-ink">{trace.data.task.tool_name}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Created</dt>
              <dd className="text-ink">{formatDateTime(trace.data.task.created_at)}</dd>
            </div>
          </dl>
          {trace.data.events.length === 0 ? (
            <p className="text-sm text-muted">No events recorded for this task.</p>
          ) : (
            <ol aria-label="Task events" className="flex flex-col gap-2">
              {trace.data.events.map((event) => (
                <li key={event.id} className="flex flex-col gap-1 rounded-lg border border-line bg-surface p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-xs text-ink">{event.type}</code>
                    <span className="text-xs text-muted">{formatDateTime(event.created_at)}</span>
                  </div>
                  <p className="text-muted">{event.message}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </div>
  );
}
