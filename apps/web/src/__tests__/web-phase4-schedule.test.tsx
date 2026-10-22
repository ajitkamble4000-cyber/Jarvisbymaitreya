import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SchedulePreview } from "../pages/schedule/SchedulePreview";
import { defaultScheduleForm, previewSpec } from "../pages/schedule/scheduleModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const NOW = Date.parse("2026-10-21T12:00:00Z");
const base = { ...defaultScheduleForm(NOW, "Europe/London"), timeOfDay: "07:30", weekdays: [1, 3] };

describe("previewSpec", () => {
  it("builds the daily and weekly timing without needing a name or a tool", () => {
    expect(previewSpec(base, NOW)).toEqual({ kind: "daily", time_of_day: "07:30", timezone: "Europe/London" });
    expect(previewSpec({ ...base, kind: "weekly", weekdays: [3, 1] }, NOW)).toEqual({
      kind: "weekly",
      time_of_day: "07:30",
      weekdays: [1, 3],
      timezone: "Europe/London",
    });
  });

  it("returns nothing for an unfinished or invalid timing", () => {
    expect(previewSpec({ ...base, timeOfDay: "7:30" }, NOW)).toBeNull();
    expect(previewSpec({ ...base, timezone: "Mars/Olympus" }, NOW)).toBeNull();
    expect(previewSpec({ ...base, kind: "weekly", weekdays: [] }, NOW)).toBeNull();
    expect(previewSpec({ ...base, kind: "once", runAt: "" }, NOW)).toBeNull();
    expect(previewSpec({ ...base, kind: "once", runAt: "2020-01-01T09:00" }, NOW)).toBeNull();
  });

  it("builds a one-time spec only for a future instant", () => {
    const later = new Date(NOW + 86_400_000).toISOString().slice(0, 16);
    const spec = previewSpec({ ...base, kind: "once", runAt: later }, NOW);
    expect(spec?.kind).toBe("once");
    expect(spec?.run_at).toBeDefined();
  });
});

describe("SchedulePreview", () => {
  it("asks the user for a time when there is none", () => {
    renderWithProviders(<SchedulePreview spec={null} />);
    expect(screen.getByText("Enter a time to see the next runs.")).toBeInTheDocument();
  });

  it("lists the runs the server returns, once the timing has settled", async () => {
    const bodies: unknown[] = [];
    routeFetch({
      "/schedules/preview": (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return { runs: ["2026-10-22T06:30:00Z", "2026-10-23T06:30:00Z"] };
      },
    });
    renderWithProviders(<SchedulePreview spec={{ kind: "daily", time_of_day: "07:30", timezone: "Europe/London" }} />);

    expect(await screen.findAllByRole("listitem", {}, { timeout: 3000 })).toHaveLength(2);
    expect(bodies).toEqual([{ kind: "daily", time_of_day: "07:30", timezone: "Europe/London" }]);
    expect(screen.getByText(/The schedule itself runs in Europe\/London/)).toBeInTheDocument();
  });

  it("says when no run is left", async () => {
    routeFetch({ "/schedules/preview": () => ({ runs: [] }) });
    renderWithProviders(<SchedulePreview spec={{ kind: "once", run_at: "2026-10-30T09:00:00Z", timezone: "UTC" }} />);
    expect(await screen.findByText("No further runs.", {}, { timeout: 3000 })).toBeInTheDocument();
  });

  it("shows the server's message when the spec is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "invalid_time", message: "Enter a valid time zone." } }), { status: 422 })),
    );
    renderWithProviders(<SchedulePreview spec={{ kind: "daily", time_of_day: "07:30", timezone: "UTC" }} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid time zone."), { timeout: 3000 });
    vi.unstubAllGlobals();
  });
});
