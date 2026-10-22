import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScheduleForm } from "../pages/schedule/ScheduleForm";
import { argumentFields, buildArguments, defaultScheduleForm, validateScheduleForm, type ScheduleFormState } from "../pages/schedule/scheduleModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";
import type { ToolOut } from "../types/api";

const NOW = Date.parse("2026-10-19T10:00:00Z");

const tool: ToolOut = {
  name: "system_info",
  title: "System information",
  description: "Shows basic system information.",
  category: "system",
  risk_level: "low",
  target: "server",
  requires_device: false,
  approval_required: false,
  status: "working",
  example_commands: [],
  notes: "",
  parameters: {
    type: "object",
    properties: {
      detail: { type: "string", description: "How much to include" },
      limit: { type: "integer" },
      mode: { type: "string", enum: ["short", "long"] },
      nested: { type: "object" },
    },
    required: ["mode"],
  },
};

function filledForm(overrides: Partial<ScheduleFormState> = {}): ScheduleFormState {
  return {
    ...defaultScheduleForm(NOW, "UTC"),
    name: "Morning check",
    toolName: tool.name,
    values: { detail: "", limit: "", mode: "short" },
    ...overrides,
  };
}

describe("Schedule form validation", () => {
  it("requires a future time for a one-time schedule", () => {
    const past = validateScheduleForm(filledForm({ kind: "once", runAt: "2026-10-01T09:00" }), { now: NOW, tools: [tool], devices: [] });
    expect(past.ok).toBe(false);
    if (!past.ok) expect(past.errors.runAt).toBe("Choose a time in the future.");

    const future = validateScheduleForm(filledForm({ kind: "once", runAt: "2031-01-01T09:00" }), { now: NOW, tools: [tool], devices: [] });
    expect(future.ok).toBe(true);
    if (future.ok) {
      expect(future.body.schedule.kind).toBe("once");
      expect(future.body.schedule.run_at).toMatch(/Z$/);
      expect(future.body.schedule).not.toHaveProperty("weekdays");
    }
  });

  it("requires at least one weekday for a weekly schedule", () => {
    const none = validateScheduleForm(filledForm({ kind: "weekly", weekdays: [], timeOfDay: "08:30" }), { now: NOW, tools: [tool], devices: [] });
    expect(none.ok).toBe(false);
    if (!none.ok) expect(none.errors.weekdays).toBe("Choose at least one weekday.");

    const some = validateScheduleForm(filledForm({ kind: "weekly", weekdays: [4, 0], timeOfDay: "08:30" }), { now: NOW, tools: [tool], devices: [] });
    expect(some.ok).toBe(true);
    if (some.ok) expect(some.body.schedule).toEqual({ kind: "weekly", time_of_day: "08:30", weekdays: [0, 4], timezone: "UTC" });
  });

  it("rejects an invalid time zone and an out-of-range retry", () => {
    const result = validateScheduleForm(filledForm({ timezone: "Mars/Olympus", maxAttempts: "9", backoffSeconds: "-1" }), {
      now: NOW,
      tools: [tool],
      devices: [],
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.timezone).toBe("Enter a valid time zone, such as Europe/London.");
      expect(result.errors.maxAttempts).toBe("Enter a whole number of attempts from 1 to 5.");
      expect(result.errors.backoffSeconds).toBe("Enter a whole number of seconds from 0 to 86400.");
    }
  });

  it("builds typed arguments from the tool schema and asks for required values", () => {
    const { fields, unsupported } = argumentFields(tool.parameters);
    expect(fields.map((field) => field.name).sort()).toEqual(["detail", "limit", "mode"]);
    expect(unsupported).toEqual([{ name: "nested", required: false }]);

    const built = buildArguments(fields, { detail: "", limit: "3", mode: "" });
    expect(built.args).toEqual({ limit: 3 });
    expect(built.errors).toEqual({ "arg:mode": "Enter a value for Mode." });
  });
});

describe("ScheduleForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the weekday error and does not create a schedule when no weekday is chosen", async () => {
    const { mock, calls } = routeFetch({
      "/tools": () => ({ items: [tool] }),
      "/devices": () => ({ items: [] }),
      "/schedules": () => ({ id: "x", name: "Morning check" }),
    });
    const user = userEvent.setup();
    renderWithProviders(<ScheduleForm onCancel={() => undefined} onSaved={() => undefined} />, { route: "/schedule" });

    await screen.findByLabelText("Tool");
    await user.type(screen.getByLabelText("Name"), "Morning check");
    await user.selectOptions(screen.getByLabelText("Tool"), tool.name);
    await user.selectOptions(await screen.findByLabelText("Mode"), "short");
    await user.selectOptions(screen.getByLabelText("Schedule"), "weekly");
    await user.click(screen.getByRole("button", { name: "Create schedule" }));

    expect(await screen.findByText("Choose at least one weekday.")).toBeInTheDocument();
    expect(screen.getByText("One field needs attention.")).toBeInTheDocument();
    // The next-runs preview may still ask the server about the timing it had before the weekday was cleared.
    // What must never happen is a create request, which is a POST to the collection itself.
    const posted = mock.mock.calls.find(([input, init]) => String(input).endsWith("/api/v1/schedules") && (init as RequestInit | undefined)?.method === "POST");
    expect(posted).toBeUndefined();
    expect(calls.includes("/schedules")).toBe(false);
  });

  it("names the one-time problem in the form when the chosen time has passed", async () => {
    routeFetch({ "/tools": () => ({ items: [tool] }), "/devices": () => ({ items: [] }) });
    const user = userEvent.setup();
    renderWithProviders(<ScheduleForm onCancel={() => undefined} onSaved={() => undefined} />, { route: "/schedule" });

    await screen.findByLabelText("Tool");
    await user.type(screen.getByLabelText("Name"), "Once only");
    await user.selectOptions(screen.getByLabelText("Tool"), tool.name);
    await user.selectOptions(screen.getByLabelText("Schedule"), "once");
    fireEvent.change(screen.getByLabelText("Date and time"), { target: { value: "2001-01-01T09:00" } });
    await user.click(screen.getByRole("button", { name: "Create schedule" }));

    await waitFor(() => expect(screen.getByText("Choose a time in the future.")).toBeInTheDocument());
  });
});
