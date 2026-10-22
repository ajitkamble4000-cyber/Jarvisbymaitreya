import { useMemo } from "react";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { formatDateTime } from "../../services/format";
import type { ScheduleCreateBody } from "./scheduleModel";
import { useSchedulePreview } from "./scheduleQueries";

type TimingSpec = ScheduleCreateBody["schedule"];

/**
 * The next runs of the timing being entered. The spec is debounced, so typing does not send a request on every
 * key. The server works the runs out with the same rules as the scheduler, and nothing is saved.
 */
export function SchedulePreview({ spec }: { spec: TimingSpec | null }) {
  const key = spec ? JSON.stringify(spec) : "";
  const settledKey = useDebouncedValue(key, 400);
  const settled = useMemo<TimingSpec | null>(() => (settledKey ? (JSON.parse(settledKey) as TimingSpec) : null), [settledKey]);
  const preview = useSchedulePreview(settled);

  if (!spec) {
    return <p className="text-sm text-muted">Enter a time to see the next runs.</p>;
  }

  const waiting = key !== settledKey || preview.isPending;
  return (
    <section aria-labelledby="schedule-preview-title" className="flex flex-col gap-2 rounded-lg border border-line bg-raised p-3">
      <h3 id="schedule-preview-title" className="text-sm font-semibold text-ink">
        Next runs
      </h3>
      {preview.isError ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {preview.error.message}
        </p>
      ) : waiting ? (
        <p role="status" className="text-sm text-muted">
          Working out the next runs…
        </p>
      ) : preview.data && preview.data.runs.length > 0 ? (
        <ol className="list-decimal pl-5 text-sm text-ink">
          {preview.data.runs.map((run) => (
            <li key={run}>{formatDateTime(run)}</li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted">No further runs.</p>
      )}
      <p className="text-xs text-muted">Shown in this browser's time zone. The schedule itself runs in {spec.timezone}.</p>
    </section>
  );
}
