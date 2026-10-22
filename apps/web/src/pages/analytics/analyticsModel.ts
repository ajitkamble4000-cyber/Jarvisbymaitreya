/**
 * Analytics model: the response types, source labels, value formatting and the chart inputs.
 * A metric that is null is shown as unavailable and is never turned into a zero. Charts use only
 * measured numbers.
 */

import type { BadgeTone } from "../../components/ui/Badge";

export type MetricSource = "measured" | "estimated" | "unavailable";

export interface Metric {
  value: number | null | Array<Record<string, unknown>>;
  unit: string;
  source: MetricSource | string;
  note?: string | null;
}

export interface ProviderBreakdown {
  provider_id: string;
  requests: Metric;
  failures: Metric;
  avg_latency_ms: Metric;
  total_tokens: Metric;
}

export interface AnalyticsSummary {
  period_days: number;
  generated_at: string;
  sections: {
    tasks: Record<"total" | "completed" | "failed" | "cancelled" | "awaiting_approval" | "success_rate", Metric>;
    latency: { avg_device_task_seconds: Metric };
    ai: Record<"requests" | "failures" | "avg_latency_ms" | "input_tokens" | "output_tokens" | "total_tokens" | "tokens_estimated_share", Metric> & {
      by_provider: ProviderBreakdown[];
    };
    cost: { estimated_usd: Metric };
    devices: Record<"total" | "online" | "offline" | "revoked", Metric>;
    integrations: Record<"connected" | "implemented", Metric>;
    errors: Record<"task_error_codes" | "ai_error_codes", Metric>;
  };
}

export const SOURCE_LABEL: Record<string, string> = {
  measured: "Measured",
  estimated: "Estimated",
  unavailable: "Unavailable",
};

export const SOURCE_TONE: Record<string, BadgeTone> = {
  measured: "success",
  estimated: "warning",
  unavailable: "neutral",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? "Unknown source";
}

/** True when the metric has a usable number. Null and unavailable metrics never do. */
export function hasValue(metric: Metric): boolean {
  return metric.source !== "unavailable" && typeof metric.value === "number" && Number.isFinite(metric.value);
}

/** Formats one metric for display. Missing values say "Unavailable". */
export function formatMetric(metric: Metric): string {
  if (!hasValue(metric)) return "Unavailable";
  const value = metric.value as number;
  switch (metric.unit) {
    case "ratio":
      return `${(value * 100).toFixed(1)}%`;
    case "ms":
      return `${Math.round(value).toLocaleString()} ms`;
    case "seconds":
      return `${value.toFixed(1)} s`;
    case "usd":
      return `$${value.toFixed(2)}`;
    default:
      return `${value.toLocaleString()} ${metric.unit}`;
  }
}

export interface ChartBar {
  label: string;
  value: number;
}

/** Bars built from measured numbers only. Metrics that are not measured are left out and listed separately. */
export function measuredBars(entries: ReadonlyArray<{ label: string; metric: Metric }>): { bars: ChartBar[]; omitted: string[] } {
  const bars: ChartBar[] = [];
  const omitted: string[] = [];
  for (const entry of entries) {
    if (hasValue(entry.metric) && entry.metric.source === "measured") {
      bars.push({ label: entry.label, value: entry.metric.value as number });
    } else {
      omitted.push(entry.label);
    }
  }
  return { bars, omitted };
}

/** The bar scale: the largest value, never below 1 so an all-zero chart still draws a baseline. */
export function barScale(bars: readonly ChartBar[]): number {
  return Math.max(1, ...bars.map((bar) => bar.value));
}

export const PERIOD_OPTIONS = [
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
] as const;
