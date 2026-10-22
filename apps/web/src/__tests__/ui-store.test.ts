import { describe, expect, it, vi } from "vitest";
import { createUiStore, fromAppearanceSettings, sanitize, toAppearancePatch, DEFAULT_UI_PREFERENCES } from "../stores/ui-store";
import { onAccentColor } from "../providers/ThemeProvider";
import type { AppearanceSettings } from "../types/api";

const appearance: AppearanceSettings = {
  theme: "light",
  accent_color: "#ff6600",
  orb_style: "ring",
  animation_intensity: "low",
  reduced_motion: "on",
  font_size: "large",
  sidebar_hidden: ["files"],
  dashboard_widgets: { status: true, devices: true, tasks: true, activity: true, schedule: true, quick_actions: true },
};

describe("ui store", () => {
  it("drops values that are not valid, so a tampered localStorage entry cannot break the UI", () => {
    expect(sanitize({ theme: "neon", accentColor: "javascript:1", fontSize: "huge", hiddenNav: ["chat"] })).toEqual({ hiddenNav: ["chat"] });
    expect(sanitize({ accentColor: "#22d3ee" })).toEqual({ accentColor: "#22d3ee" });
  });

  it("hydrates from settings.appearance and maps keys both ways", () => {
    const store = createUiStore();
    store.hydrateFromServer(appearance);
    expect(store.getState()).toMatchObject({ theme: "light", accentColor: "#ff6600", reducedMotion: "on", hiddenNav: ["files"] });
    expect(toAppearancePatch({ accentColor: "#000000", hiddenNav: [] })).toEqual({ accent_color: "#000000", sidebar_hidden: [] });
    expect(fromAppearanceSettings(appearance).orbStyle).toBe("ring");
  });

  it("syncs appearance changes to the settings API but not sidebar order, which is local only", () => {
    const store = createUiStore({ ...DEFAULT_UI_PREFERENCES });
    const handler = vi.fn();
    store.setSyncHandler(handler);
    store.setPreferences({ sidebarOrder: ["tasks", "chat"] });
    expect(handler).not.toHaveBeenCalled();
    store.setPreferences({ theme: "system" });
    expect(handler).toHaveBeenCalledWith({ theme: "system" });
  });

  it("picks dark or light text for the accent colour so buttons stay readable", () => {
    expect(onAccentColor("#22d3ee")).toBe("#04171d");
    expect(onAccentColor("#1d4ed8")).toBe("#ffffff");
  });
});
