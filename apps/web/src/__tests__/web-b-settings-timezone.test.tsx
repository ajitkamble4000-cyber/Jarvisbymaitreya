import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AutomationSection } from "../pages/settings/AutomationSection";
import { isKnownTimeZone, timezoneFromInput, timezoneIsDirty, UTC_OPTION_LABEL } from "../pages/settings/timezoneModel";
import { api } from "../services/api";
import type { SettingsDoc } from "../types/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const automation: SettingsDoc["automation"] = {
  max_concurrent_tasks: 3,
  default_task_timeout_seconds: 120,
  disabled_tools: [],
  emergency_stopped: false,
};

const LABEL = "Timezone for reminders and schedules";

describe("Settings: timezone for reminders and schedules", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("offers the browser's time zone list, with a blank option for UTC", () => {
    routeFetch({ "/tools": () => ({ items: [] }) });
    renderWithProviders(<AutomationSection automation={automation} timezone={null} />, { route: "/settings?tab=automation" });

    const select = screen.getByRole("combobox", { name: LABEL });
    expect(within(select).getByRole("option", { name: UTC_OPTION_LABEL })).toHaveValue("");
    expect(within(select).getByRole("option", { name: "Europe/London" })).toBeInTheDocument();
    // Both forms in the section (limits and time zone) show Save changes, and neither is dirty yet.
    for (const button of screen.getAllByRole("button", { name: "Save changes" })) expect(button).toBeDisabled();
  });

  it("saves only the top-level timezone key, and nothing else", async () => {
    routeFetch({ "/tools": () => ({ items: [] }) });
    const patch = vi.spyOn(api, "patch").mockResolvedValue({} as SettingsDoc);
    const user = userEvent.setup();
    renderWithProviders(<AutomationSection automation={automation} timezone={null} />, { route: "/settings?tab=automation" });

    await user.selectOptions(screen.getByRole("combobox", { name: LABEL }), "Europe/London");
    const enabledSave = screen.getAllByRole("button", { name: "Save changes" }).find((button) => !button.hasAttribute("disabled"));
    expect(enabledSave).toBeDefined();
    await user.click(enabledSave as HTMLElement);

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith("/settings", { timezone: "Europe/London" });
  });

  it("saves a blank choice as null, which is UTC", async () => {
    routeFetch({ "/tools": () => ({ items: [] }) });
    const patch = vi.spyOn(api, "patch").mockResolvedValue({} as SettingsDoc);
    const user = userEvent.setup();
    renderWithProviders(<AutomationSection automation={automation} timezone="Asia/Kolkata" />, { route: "/settings?tab=automation" });

    const select = screen.getByRole("combobox", { name: LABEL });
    expect(select).toHaveValue("Asia/Kolkata");
    await user.selectOptions(select, "");
    const enabledSave = screen.getAllByRole("button", { name: "Save changes" }).find((button) => !button.hasAttribute("disabled"));
    await user.click(enabledSave as HTMLElement);

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith("/settings", { timezone: null });
  });

  it("falls back to a text field when the browser cannot list time zones, and checks the name before saving", async () => {
    const original = Object.getOwnPropertyDescriptor(Intl, "supportedValuesOf");
    Object.defineProperty(Intl, "supportedValuesOf", { value: undefined, configurable: true, writable: true });
    try {
      routeFetch({ "/tools": () => ({ items: [] }) });
      const patch = vi.spyOn(api, "patch").mockResolvedValue({} as SettingsDoc);
      const user = userEvent.setup();
      renderWithProviders(<AutomationSection automation={automation} timezone={null} />, { route: "/settings?tab=automation" });

      const input = screen.getByRole("textbox", { name: LABEL });
      expect(screen.queryByRole("combobox", { name: LABEL })).toBeNull();

      await user.type(input, "Mars/Olympus");
      await user.click(screen.getAllByRole("button", { name: "Save changes" }).find((button) => !button.hasAttribute("disabled")) as HTMLElement);
      expect(await screen.findByText("Use an IANA time zone name such as Europe/London, or leave blank for UTC.")).toBeInTheDocument();
      expect(patch).not.toHaveBeenCalled();

      await user.clear(input);
      await user.type(input, "Asia/Kolkata");
      await user.click(screen.getAllByRole("button", { name: "Save changes" }).find((button) => !button.hasAttribute("disabled")) as HTMLElement);
      await waitFor(() => expect(patch).toHaveBeenCalledWith("/settings", { timezone: "Asia/Kolkata" }));
    } finally {
      if (original) Object.defineProperty(Intl, "supportedValuesOf", original);
    }
  });

  it("maps the field text to the saved value, with blank meaning UTC", () => {
    expect(timezoneFromInput("   ")).toEqual({ value: null, error: null });
    expect(timezoneFromInput(" Europe/London ")).toEqual({ value: "Europe/London", error: null });
    expect(timezoneFromInput("Nowhere/Nothing").error).toMatch(/IANA time zone/);
    expect(timezoneFromInput("x".repeat(65)).error).toBe("Use 64 characters or fewer.");
    expect(isKnownTimeZone("UTC")).toBe(true);
    expect(timezoneIsDirty("", null)).toBe(false);
    expect(timezoneIsDirty("Europe/London", null)).toBe(true);
    expect(timezoneIsDirty("Europe/London", "Europe/London")).toBe(false);
  });
});
