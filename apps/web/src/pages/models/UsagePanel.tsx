import { useState } from "react";
import { Badge } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { ErrorState } from "../../components/ui/ErrorState";
import { Select } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { formatNumber, humanize } from "../../services/format";
import { useProviderUsage } from "../../services/queries";
import { costLabel, tokenSourceLabel } from "./providerModel";

const PERIODS = [7, 30, 90] as const;

/** Usage for the period. Every figure carries its source: measured, estimated or unavailable. */
export function UsagePanel() {
  const [days, setDays] = useState<number>(30);
  const usage = useProviderUsage(days);

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>Usage</CardTitle>
        <label className="flex items-center gap-2 text-sm text-muted">
          Period
          <Select className="h-8 w-28 text-xs" value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {PERIODS.map((period) => (
              <option key={period} value={period}>
                Last {period} days
              </option>
            ))}
          </Select>
        </label>
      </div>

      {usage.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : usage.isError ? (
        <ErrorState message={usage.error.message} onRetry={() => void usage.refetch()} />
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-muted">Requests</dt>
              <dd className="text-ink tabular-nums">
                {formatNumber(usage.data.totals.requests)} <Badge tone="success">measured</Badge>
              </dd>
            </div>
            <div>
              <dt className="text-muted">Failures</dt>
              <dd className="text-ink tabular-nums">{formatNumber(usage.data.totals.failures)}</dd>
            </div>
            <div>
              <dt className="text-muted">Tokens</dt>
              <dd className="text-ink tabular-nums">
                {formatNumber(usage.data.totals.total_tokens)}{" "}
                <Badge tone={tokenSourceLabel(usage.data) === "measured" ? "success" : "warning"}>{tokenSourceLabel(usage.data)}</Badge>
              </dd>
            </div>
            <div>
              <dt className="text-muted">Estimated cost</dt>
              <dd className="text-ink">
                <Badge tone="neutral">{costLabel(usage.data)}</Badge>
              </dd>
            </div>
          </dl>
          <p className="text-xs text-muted">
            Average latency {usage.data.avg_latency_ms === null ? "unavailable" : `${usage.data.avg_latency_ms} ms`}. {usage.data.cost_note}
          </p>
          {usage.data.by_provider.length > 0 ? (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Requests by provider</caption>
              <thead className="text-xs text-muted">
                <tr>
                  <th scope="col" className="py-1 font-medium">Provider</th>
                  <th scope="col" className="py-1 font-medium">Requests</th>
                  <th scope="col" className="py-1 font-medium">Failures</th>
                </tr>
              </thead>
              <tbody>
                {usage.data.by_provider.map((row) => (
                  <tr key={row.provider_id ?? "unknown"} className="border-t border-line">
                    <td className="py-1.5 text-ink">{humanize(row.provider_id ?? "unknown")}</td>
                    <td className="py-1.5 tabular-nums">{formatNumber(row.requests)}</td>
                    <td className="py-1.5 tabular-nums">{formatNumber(row.failures ?? null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-muted">No requests in this period.</p>
          )}
        </>
      )}
    </Card>
  );
}
