import { useState } from "react";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Switch } from "../../components/ui/Switch";
import { formatDateTime } from "../../services/format";
import { LOG_LEVELS, LOG_LIMIT, useLogsQuery, type LogLevel } from "./developerQueries";

const LEVEL_TONE: Record<string, BadgeTone> = {
  DEBUG: "neutral",
  INFO: "info",
  WARNING: "warning",
  ERROR: "danger",
  CRITICAL: "danger",
};

/** Recent backend log lines, filtered by level. Values are redacted by the API. */
export function LogsPanel() {
  const [level, setLevel] = useState<LogLevel>("INFO");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const logs = useLogsQuery(level, autoRefresh);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Minimum level">
          {(control) => (
            <Select {...control} value={level} onChange={(event) => setLevel(event.target.value as LogLevel)}>
              {LOG_LEVELS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Switch label="Refresh every 5 seconds" checked={autoRefresh} onCheckedChange={setAutoRefresh} />
        <Button variant="secondary" size="sm" loading={logs.isFetching} onClick={() => void logs.refetch()}>
          Refresh now
        </Button>
      </div>
      <p className="text-xs text-muted">Showing up to {LOG_LIMIT} lines at {level} or above.</p>

      {logs.isPending ? (
        <SkeletonList rows={4} label="Loading logs" />
      ) : logs.isError ? (
        <ErrorState message={logs.error.message} onRetry={() => void logs.refetch()} />
      ) : (logs.data ?? []).length === 0 ? (
        <EmptyState title="No log lines" description="Nothing at this level has been logged recently." />
      ) : (
        <ol aria-label="Log lines" className="flex flex-col gap-1 font-mono text-xs">
          {(logs.data ?? []).map((line, index) => (
            <li key={`${line.ts}-${index}`} className="flex flex-col gap-0.5 rounded border border-line bg-surface p-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={LEVEL_TONE[line.level] ?? "neutral"}>{line.level}</Badge>
                <span className="text-muted">{formatDateTime(line.ts)}</span>
                <span className="text-muted">{line.logger}</span>
              </div>
              <span className="whitespace-pre-wrap break-words text-ink">{line.message}</span>
              {line.task_id || line.request_id || line.device_id || line.provider_id ? (
                <span className="text-muted">
                  {[
                    line.task_id ? `task ${line.task_id}` : null,
                    line.device_id ? `device ${line.device_id}` : null,
                    line.provider_id ? `provider ${line.provider_id}` : null,
                    line.request_id ? `request ${line.request_id}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
