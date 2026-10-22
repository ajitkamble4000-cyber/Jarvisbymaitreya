import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Tabs } from "../../components/ui/Tabs";
import { api } from "../../services/api";
import { formatDateTime, humanize } from "../../services/format";
import {
  PERIOD_OPTIONS,
  SOURCE_TONE,
  barScale,
  formatMetric,
  measuredBars,
  sourceLabel,
  type AnalyticsSummary,
  type Metric,
} from "./analyticsModel";

/** Simple SVG bars drawn from measured numbers. There is no chart library. */
export function MetricBars({ label, bars, unit }: { label: string; bars: ReturnType<typeof measuredBars>["bars"]; unit: string }) {
  const scale = barScale(bars);
  const rowHeight = 28;
  const labelWidth = 140;
  const width = 360;
  const barWidth = width - labelWidth - 60;
  const height = bars.length * rowHeight + 4;
  return (
    <figure className="flex flex-col gap-2">
      <svg role="img" aria-label={`${label}, ${unit}`} viewBox={`0 0 ${width} ${height}`} className="w-full max-w-md text-ink">
        {bars.map((bar, index) => {
          const y = index * rowHeight + 4;
          const length = Math.round((bar.value / scale) * barWidth);
          return (
            <g key={bar.label}>
              <text x={0} y={y + 14} fontSize="12" fill="currentColor">
                {bar.label}
              </text>
              <rect x={labelWidth} y={y} width={barWidth} height={16} rx={4} className="fill-raised" />
              <rect x={labelWidth} y={y} width={Math.max(length, 0)} height={16} rx={4} className="fill-accent" />
              <text x={labelWidth + barWidth + 8} y={y + 13} fontSize="12" fill="currentColor">
                {bar.value.toLocaleString()}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

function MetricRow({ label, metric }: { label: string; metric: Metric }) {
  const unavailable = metric.source === "unavailable" || metric.value === null;
  return (
    <div className="flex flex-col gap-1 border-b border-line py-2 last:border-b-0 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2">
        <span className={unavailable ? "text-sm text-muted" : "text-sm font-medium text-ink"}>{formatMetric(metric)}</span>
        <Badge tone={SOURCE_TONE[metric.source] ?? "neutral"}>{sourceLabel(metric.source)}</Badge>
        {metric.note ? <span className="w-full text-xs text-muted">{metric.note}</span> : null}
      </dd>
    </div>
  );
}

const TASK_LABELS = {
  total: "Tasks created",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  awaiting_approval: "Waiting for approval",
  success_rate: "Success rate",
} as const;

/** Analytics: task, AI, device, integration and error numbers for a period. Unavailable is never shown as zero. */
export default function AnalyticsPage() {
  const [days, setDays] = useState<string>("30");
  const summary = useQuery({
    queryKey: ["analytics", "summary", days] as const,
    queryFn: () => api.get<AnalyticsSummary>(`/analytics/summary?days=${days}`),
    placeholderData: keepPreviousData,
  });
  const data = summary.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Analytics"
        description="Numbers from this account's own records. Each value says whether it was measured, estimated or unavailable."
      />

      <Tabs label="Period" value={days} onChange={setDays} items={PERIOD_OPTIONS.map((option) => ({ id: option.id, label: option.label }))} />

      {summary.isPending ? (
        <SkeletonList rows={4} label="Loading analytics" />
      ) : summary.isError ? (
        <ErrorState message={summary.error.message} onRetry={() => void summary.refetch()} />
      ) : !data ? null : (
        <>
          <p className="text-xs text-muted">
            Last {data.period_days} days, generated {formatDateTime(data.generated_at)}.
          </p>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="flex flex-col gap-4">
              <CardTitle>Tasks</CardTitle>
              <dl className="flex flex-col">
                {(Object.keys(TASK_LABELS) as Array<keyof typeof TASK_LABELS>).map((key) => (
                  <MetricRow key={key} label={TASK_LABELS[key]} metric={data.sections.tasks[key]} />
                ))}
              </dl>
              {(() => {
                const { bars, omitted } = measuredBars([
                  { label: "Completed", metric: data.sections.tasks.completed },
                  { label: "Failed", metric: data.sections.tasks.failed },
                  { label: "Cancelled", metric: data.sections.tasks.cancelled },
                  { label: "Waiting", metric: data.sections.tasks.awaiting_approval },
                ]);
                if (bars.length === 0 || bars.every((bar) => bar.value === 0)) {
                  return <EmptyState title="No finished tasks in this period" description="The chart fills in when tasks finish." />;
                }
                return (
                  <>
                    <MetricBars label="Task outcomes" bars={bars} unit="tasks" />
                    {omitted.length > 0 ? <p className="text-xs text-muted">Not charted: {omitted.join(", ")}.</p> : null}
                  </>
                );
              })()}
              <p className="text-sm text-muted">
                Average device task time: <span className="text-ink">{formatMetric(data.sections.latency.avg_device_task_seconds)}</span>
              </p>
            </Card>

            <Card className="flex flex-col gap-4">
              <CardTitle>AI requests</CardTitle>
              <dl className="flex flex-col">
                <MetricRow label="Requests" metric={data.sections.ai.requests} />
                <MetricRow label="Failures" metric={data.sections.ai.failures} />
                <MetricRow label="Average latency" metric={data.sections.ai.avg_latency_ms} />
                <MetricRow label="Input tokens" metric={data.sections.ai.input_tokens} />
                <MetricRow label="Output tokens" metric={data.sections.ai.output_tokens} />
                <MetricRow label="Total tokens" metric={data.sections.ai.total_tokens} />
                <MetricRow label="Share of tokens estimated" metric={data.sections.ai.tokens_estimated_share} />
                <MetricRow label="Estimated cost" metric={data.sections.cost.estimated_usd} />
              </dl>
              {data.sections.ai.by_provider.length === 0 ? (
                <EmptyState title="No provider calls in this period" description="Requests to AI providers are counted here once they are made." />
              ) : (
                <>
                  <MetricBars
                    label="Requests by provider"
                    unit="requests"
                    bars={measuredBars(
                      data.sections.ai.by_provider.map((row) => ({ label: humanize(row.provider_id), metric: row.requests })),
                    ).bars}
                  />
                  <ul className="flex flex-col gap-2 text-sm">
                    {data.sections.ai.by_provider.map((row) => (
                      <li key={row.provider_id} className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-ink">{humanize(row.provider_id)}</span>
                        <span className="text-muted">
                          {formatMetric(row.requests)} requests, {formatMetric(row.failures)} failures, average {formatMetric(row.avg_latency_ms)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </Card>

            <Card className="flex flex-col gap-4">
              <CardTitle>Devices and integrations</CardTitle>
              <dl className="flex flex-col">
                <MetricRow label="Devices" metric={data.sections.devices.total} />
                <MetricRow label="Online now" metric={data.sections.devices.online} />
                <MetricRow label="Offline now" metric={data.sections.devices.offline} />
                <MetricRow label="Revoked" metric={data.sections.devices.revoked} />
                <MetricRow label="Integrations connected" metric={data.sections.integrations.connected} />
                <MetricRow label="Integrations implemented" metric={data.sections.integrations.implemented} />
              </dl>
              {(() => {
                const { bars, omitted } = measuredBars([
                  { label: "Online", metric: data.sections.devices.online },
                  { label: "Offline", metric: data.sections.devices.offline },
                  { label: "Revoked", metric: data.sections.devices.revoked },
                ]);
                if (bars.every((bar) => bar.value === 0)) {
                  return <EmptyState title="No devices paired" description="Pair a desktop agent on the Devices page to see its status here." />;
                }
                return (
                  <>
                    <MetricBars label="Device status" bars={bars} unit="devices" />
                    {omitted.length > 0 ? <p className="text-xs text-muted">Not charted: {omitted.join(", ")}.</p> : null}
                  </>
                );
              })()}
            </Card>

            <Card className="flex flex-col gap-4">
              <CardTitle>Errors</CardTitle>
              <ErrorCodeList title="Task errors" metric={data.sections.errors.task_error_codes} />
              <ErrorCodeList title="AI errors" metric={data.sections.errors.ai_error_codes} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function ErrorCodeList({ title, metric }: { title: string; metric: Metric }) {
  if (metric.source === "unavailable" || metric.value === null || !Array.isArray(metric.value)) {
    return (
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium text-ink">{title}</h3>
        <p className="text-sm text-muted">Unavailable</p>
        {metric.note ? <p className="text-xs text-muted">{metric.note}</p> : null}
      </div>
    );
  }
  const rows = metric.value as Array<Record<string, unknown>>;
  if (rows.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium text-ink">{title}</h3>
        <p className="text-sm text-muted">No errors recorded in this period.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      <ul className="flex flex-col gap-1 text-sm">
        {rows.map((row, index) => (
          <li key={`${String(row.code)}-${index}`} className="flex justify-between gap-2">
            <code className="text-ink">{String(row.code ?? "unknown")}</code>
            <span className="text-muted">{typeof row.count === "number" ? row.count.toLocaleString() : "Unavailable"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
