import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AnalyticsPage from "../pages/analytics/AnalyticsPage";
import { formatMetric, measuredBars, type Metric } from "../pages/analytics/analyticsModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const unavailable = (unit: string, note = "No data") => ({ value: null, unit, source: "unavailable", note });
const measured = (value: number, unit: string) => ({ value, unit, source: "measured" });

const summary = {
  period_days: 30,
  generated_at: "2026-10-19T04:51:37Z",
  sections: {
    tasks: {
      total: measured(4, "tasks"),
      completed: measured(3, "tasks"),
      failed: unavailable("tasks"),
      cancelled: measured(0, "tasks"),
      awaiting_approval: measured(1, "tasks"),
      success_rate: unavailable("ratio"),
    },
    latency: { avg_device_task_seconds: unavailable("seconds") },
    ai: {
      requests: measured(0, "requests"),
      failures: unavailable("requests"),
      avg_latency_ms: unavailable("ms"),
      input_tokens: unavailable("tokens"),
      output_tokens: unavailable("tokens"),
      total_tokens: unavailable("tokens"),
      tokens_estimated_share: unavailable("ratio"),
      by_provider: [],
    },
    cost: { estimated_usd: unavailable("usd") },
    devices: { total: measured(0, "devices"), online: measured(0, "devices"), offline: measured(0, "devices"), revoked: measured(0, "devices") },
    integrations: { connected: measured(0, "integrations"), implemented: measured(0, "integrations") },
    errors: { task_error_codes: { value: [], unit: "tasks", source: "measured" }, ai_error_codes: unavailable("requests") },
  },
};

/** The table row that holds a metric label, so that the assertion reads the value next to its own label. */
function rowFor(label: string): HTMLElement {
  const row = screen.getByText(label, { selector: "dt" }).closest("div");
  if (!row) throw new Error(`No row for ${label}`);
  return row;
}

describe("Analytics unavailable values", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("formats null and unavailable metrics as Unavailable, never as zero", () => {
    const nullMetric: Metric = { value: null, unit: "usd", source: "unavailable" };
    expect(formatMetric(nullMetric)).toBe("Unavailable");
    expect(formatMetric({ value: 0, unit: "tasks", source: "measured" })).toBe("0 tasks");
    expect(formatMetric({ value: 0.5, unit: "ratio", source: "estimated" })).toBe("50.0%");
  });

  it("charts only measured numbers and says what it left out", () => {
    const { bars, omitted } = measuredBars([
      { label: "Completed", metric: measured(3, "tasks") },
      { label: "Failed", metric: unavailable("tasks") },
      { label: "Estimated", metric: { value: 9, unit: "tasks", source: "estimated" } },
    ]);
    expect(bars).toEqual([{ label: "Completed", value: 3 }]);
    expect(omitted).toEqual(["Failed", "Estimated"]);
  });

  it("renders Unavailable for null values on the page, not a zero", async () => {
    routeFetch({ "/analytics/summary": () => summary });
    renderWithProviders(<AnalyticsPage />, { route: "/analytics" });

    expect(await screen.findByText("Tasks created")).toBeInTheDocument();

    // Each unavailable row shows the value and its source badge, both as "Unavailable".
    for (const label of ["Failed", "Success rate", "Estimated cost", "Total tokens"]) {
      const row = rowFor(label);
      expect(within(row).getAllByText("Unavailable").length).toBeGreaterThan(0);
      expect(row.textContent).not.toMatch(/\b0(\.0)?\b|\$0|0%/);
    }

    // The measured counts still show their numbers.
    expect(within(rowFor("Completed")).getByText("3 tasks")).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).toBeNull();
    expect(screen.queryByText("0.0%")).toBeNull();
    expect(screen.queryByText("0 ms")).toBeNull();
  });

  it("says which outcome was left out of the chart", async () => {
    routeFetch({ "/analytics/summary": () => summary });
    renderWithProviders(<AnalyticsPage />, { route: "/analytics" });

    expect(await screen.findByText(/Not charted: Failed/)).toBeInTheDocument();
  });
});
