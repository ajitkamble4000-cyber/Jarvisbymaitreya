import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotificationsSection } from "../pages/settings/NotificationsSection";
import { parseCallLimit } from "../pages/settings/settingsModel";
import { browserAlertFor, type AlertSettings } from "../services/browserNotifications";
import { clockAt, isInWindow, parseClock, quietAt } from "../services/quietHours";
import type { LiveEvent, NotificationSettings } from "../types/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const failed: LiveEvent = { type: "task.updated", data: { task_id: "t1", status: "failed" } };
const allOn: AlertSettings = {
  desktop_notifications: true,
  on_approval_required: true,
  on_task_completed: true,
  on_task_failed: true,
  include_task_details: false,
};

describe("quiet hours window", () => {
  it("reads only valid clock times", () => {
    expect(parseClock("22:30")).toBe(22 * 60 + 30);
    expect(parseClock("00:00")).toBe(0);
    expect(parseClock("24:00")).toBeNull();
    expect(parseClock("7:00")).toBeNull();
    expect(parseClock(null)).toBeNull();
  });

  it("includes the start, excludes the end, and wraps past midnight when the start is later", () => {
    const day = (minutes: number) => isInWindow(minutes, parseClock("13:00")!, parseClock("14:00")!);
    expect(day(13 * 60)).toBe(true);
    expect(day(14 * 60)).toBe(false);
    expect(day(12 * 60 + 59)).toBe(false);

    const overnight = (minutes: number) => isInWindow(minutes, parseClock("22:30")!, parseClock("07:00")!);
    expect(overnight(23 * 60)).toBe(true);
    expect(overnight(6 * 60 + 59)).toBe(true);
    expect(overnight(7 * 60)).toBe(false);
    expect(overnight(12 * 60)).toBe(false);
  });

  it("never pauses when the window is off, incomplete or empty", () => {
    const now = new Date("2026-10-21T02:00:00Z");
    expect(quietAt({ enabled: false, start: "00:00", end: "23:59" }, now, "UTC")).toBe(false);
    expect(quietAt({ enabled: true, start: null, end: "07:00" }, now, "UTC")).toBe(false);
    expect(quietAt({ enabled: true, start: "08:00", end: "08:00" }, now, "UTC")).toBe(false);
  });

  it("reads the wall-clock time in the reminder zone", () => {
    // 22:30 UTC is 04:00 in Asia/Kolkata, so a 04:00 to 05:00 window is quiet there and not in UTC.
    const now = new Date("2026-10-21T22:30:00Z");
    const window = { enabled: true, start: "04:00", end: "05:00" };
    expect(quietAt(window, now, "Asia/Kolkata")).toBe(true);
    expect(quietAt(window, now, "UTC")).toBe(false);
    expect(clockAt(now, "Asia/Kolkata")).toEqual({ hour: 4, minute: 0 });
  });

  it("falls back to this browser's clock for an unknown zone instead of failing", () => {
    expect(() => clockAt(new Date("2026-10-21T10:00:00Z"), "Not/AZone")).not.toThrow();
    const clock = clockAt(new Date("2026-10-21T10:00:00Z"), "Not/AZone");
    expect(Number.isInteger(clock.hour) && Number.isInteger(clock.minute)).toBe(true);
  });
});

describe("quiet hours and browser alerts", () => {
  const quietSettings: AlertSettings = { ...allOn, quiet_hours_enabled: true, quiet_hours_start: "22:00", quiet_hours_end: "07:00" };

  it("makes no pop-up inside the window", () => {
    expect(browserAlertFor(failed, quietSettings, { hidden: true, timeZone: "UTC", now: new Date("2026-10-21T23:15:00Z") })).toBeNull();
  });

  it("makes a pop-up again outside the window", () => {
    expect(browserAlertFor(failed, quietSettings, { hidden: true, timeZone: "UTC", now: new Date("2026-10-21T12:00:00Z") })?.title).toBe("Task failed");
  });

  it("ignores the window when quiet hours are off", () => {
    const off = { ...quietSettings, quiet_hours_enabled: false };
    expect(browserAlertFor(failed, off, { hidden: true, timeZone: "UTC", now: new Date("2026-10-21T23:15:00Z") })?.title).toBe("Task failed");
  });
});

const notifications: NotificationSettings = {
  desktop_notifications: true,
  on_task_completed: true,
  on_task_failed: true,
  on_approval_required: true,
  on_device_disconnected: true,
  on_integration_error: true,
  on_schedule_event: true,
  on_security_event: true,
  include_task_details: false,
  quiet_hours_enabled: false,
  quiet_hours_start: null,
  quiet_hours_end: null,
};

describe("quiet hours settings form", () => {
  it("saves only the switch when the default times are kept", async () => {
    const patches: unknown[] = [];
    routeFetch({
      "/settings": (_url, init) => {
        if (init?.method === "PATCH") patches.push(JSON.parse(String(init.body)));
        return { notifications };
      },
    });
    renderWithProviders(<NotificationsSection notifications={notifications} timezone="Europe/London" />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("switch", { name: "Pause pop-ups during quiet hours" }));
    await user.click(screen.getAllByRole("button", { name: "Save changes" }).find((button) => !(button as HTMLButtonElement).disabled) as HTMLElement);

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ notifications: { quiet_hours_enabled: true } });
  });

  it("refuses a window whose start and end are the same", async () => {
    const patches: unknown[] = [];
    routeFetch({
      "/settings": (_url, init) => {
        if (init?.method === "PATCH") patches.push(JSON.parse(String(init.body)));
        return { notifications };
      },
    });
    renderWithProviders(<NotificationsSection notifications={notifications} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("switch", { name: "Pause pop-ups during quiet hours" }));
    const from = screen.getByLabelText("Quiet from");
    await user.clear(from);
    await user.type(from, "07:00");
    await user.click(screen.getAllByRole("button", { name: "Save changes" }).find((button) => !(button as HTMLButtonElement).disabled) as HTMLElement);

    expect(await screen.findByText("The quiet hours must start and end at different times.")).toBeInTheDocument();
    expect(patches).toHaveLength(0);
  });
});


describe("daily AI call limit input", () => {
  it("treats blank as no limit and accepts whole numbers from 1 to 10000", () => {
    expect(parseCallLimit("   ")).toEqual({ value: null, error: null });
    expect(parseCallLimit("25")).toEqual({ value: 25, error: null });
    expect(parseCallLimit("0").error).toMatch(/from 1 to 10000/);
    expect(parseCallLimit("10001").error).toMatch(/from 1 to 10000/);
    expect(parseCallLimit("2.5").error).toMatch(/whole number/);
    expect(parseCallLimit("many").error).toMatch(/Enter a number/);
  });
});
