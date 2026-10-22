import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActivityPage from "../pages/activity/ActivityPage";
import { auditFilterParams, auditQueryString, dateRangeParams, type AuditFilters } from "../pages/activity/activityModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const NO_FILTERS: AuditFilters = { action: "", outcome: "", from: "", to: "", offset: 0 };
const START_OF_OCT_5 = new Date(2026, 9, 5, 0, 0, 0, 0).toISOString();
const END_OF_OCT_5 = new Date(2026, 9, 5, 23, 59, 59, 999).toISOString();

describe("dateRangeParams", () => {
  it("covers whole local days, from the first moment to the last", () => {
    expect(dateRangeParams("2026-10-05", "2026-10-05")).toEqual({ since: START_OF_OCT_5, until: END_OF_OCT_5 });
  });

  it("sends only the bound that is set", () => {
    expect(dateRangeParams("2026-10-05", "")).toEqual({ since: START_OF_OCT_5 });
    expect(dateRangeParams("", "2026-10-05")).toEqual({ until: END_OF_OCT_5 });
    expect(dateRangeParams("", "")).toEqual({});
  });

  it("refuses a range that ends before it starts", () => {
    expect(dateRangeParams("2026-10-09", "2026-10-01")).toEqual({ error: "The first date is after the last date." });
  });

  it("refuses a date that does not exist", () => {
    expect(dateRangeParams("2026-02-31", "")).toEqual({ error: "The first date is not a valid date." });
    expect(dateRangeParams("", "not a date")).toEqual({ error: "The last date is not a valid date." });
  });
});

describe("audit query with dates", () => {
  it("adds nothing for dates when none are set", () => {
    expect(auditFilterParams(NO_FILTERS).toString()).toBe("");
    expect(auditQueryString(NO_FILTERS)).toBe("?limit=50&offset=0");
  });

  it("sends the same range to the list and to the export, and leaves the page out of the export", () => {
    const filters = { ...NO_FILTERS, action: "task.", from: "2026-10-05", to: "2026-10-05" };
    const exported = auditFilterParams(filters);

    expect(exported.get("action")).toBe("task.");
    expect(exported.get("since")).toBe(START_OF_OCT_5);
    expect(exported.get("until")).toBe(END_OF_OCT_5);
    expect(exported.has("limit")).toBe(false);
    expect(auditQueryString(filters)).toContain(`since=${encodeURIComponent(START_OF_OCT_5)}`);
  });

  it("leaves the range out when it cannot be used", () => {
    expect(auditFilterParams({ ...NO_FILTERS, from: "2026-10-09", to: "2026-10-01" }).toString()).toBe("");
  });
});

describe("Activity date filter", () => {
  it("asks the server for the chosen days", async () => {
    const { mock } = routeFetch({ "/audit": () => ({ items: [], total: 0 }) });
    renderWithProviders(<ActivityPage />, { route: "/activity" });
    await screen.findByText("No activity matches");

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-10-05" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-10-05" } });

    await waitFor(() => {
      const urls = mock.mock.calls.map(([input]) => String(input));
      expect(urls.some((url) => url.includes(`since=${encodeURIComponent(START_OF_OCT_5)}`) && url.includes("until="))).toBe(true);
    });
  });

  it("says when the range is backwards and does not send it", async () => {
    const { mock } = routeFetch({ "/audit": () => ({ items: [], total: 0 }) });
    renderWithProviders(<ActivityPage />, { route: "/activity" });
    await screen.findByText("No activity matches");

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-10-09" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-10-01" } });

    expect(await screen.findByText("The first date is after the last date.")).toBeInTheDocument();
    const last = String(mock.mock.calls.at(-1)?.[0]);
    expect(last).not.toContain("since=");
    expect(last).not.toContain("until=");
  });

  it("exports the CSV for the chosen days", async () => {
    const { mock } = routeFetch({
      "/audit": () => ({ items: [], total: 0 }),
      "/audit/export.csv": () => ({ ok: true }),
    });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:audit", revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWithProviders(<ActivityPage />, { route: "/activity" });
    await screen.findByText("No activity matches");
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-10-05" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-10-05" } });
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const exported = mock.mock.calls.map(([input]) => String(input)).find((url) => url.includes("/audit/export.csv"));
    expect(exported).toContain(`since=${encodeURIComponent(START_OF_OCT_5)}`);
    expect(exported).not.toContain("limit=");
  });
});
