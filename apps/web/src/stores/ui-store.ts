/**
 * UI preferences: theme, accent colour, reduced motion, orb and font settings, and the sidebar.
 *
 * Preferences are persisted to localStorage per viewer (a convenience that renders before the
 * settings request completes). Appearance preferences are also synced to the settings API:
 *   - hydrateFromServer() copies settings.appearance into the store when settings load.
 *   - setPreferences() updates the store and, for appearance keys, calls the sync handler that
 *     AppShell installs (a debounced PATCH /settings).
 * Sidebar order is local only because the API has no field for it.
 */

import { useSyncExternalStore } from "react";
import type { AnimationIntensity, AppearanceSettings, FontSizeSetting, OrbStyle, ReducedMotionSetting, ThemeSetting } from "../types/api";

export interface UiPreferences {
  theme: ThemeSetting;
  accentColor: string;
  reducedMotion: ReducedMotionSetting;
  animationIntensity: AnimationIntensity;
  orbStyle: OrbStyle;
  fontSize: FontSizeSetting;
  /** Navigation ids hidden by the viewer. Mirrors settings.appearance.sidebar_hidden. */
  hiddenNav: string[];
  /** Navigation ids in the viewer's preferred order. Local only. */
  sidebarOrder: string[];
  /** Mobile navigation sheet state. Not persisted. */
  navOpen: boolean;
}

export const DEFAULT_ACCENT = "#22d3ee";
export const UI_STORAGE_KEY = "jarvis.ui";

export const DEFAULT_UI_PREFERENCES: UiPreferences = {
  theme: "dark",
  accentColor: DEFAULT_ACCENT,
  reducedMotion: "system",
  animationIntensity: "normal",
  orbStyle: "orb",
  fontSize: "medium",
  hiddenNav: [],
  sidebarOrder: [],
  navOpen: false,
};

/** Keys that are synced to settings.appearance. */
export type AppearanceKey = "theme" | "accentColor" | "reducedMotion" | "animationIntensity" | "orbStyle" | "fontSize" | "hiddenNav";

const APPEARANCE_KEYS: readonly AppearanceKey[] = [
  "theme",
  "accentColor",
  "reducedMotion",
  "animationIntensity",
  "orbStyle",
  "fontSize",
  "hiddenNav",
];

export type AppearancePatch = Partial<Pick<AppearanceSettings, "theme" | "accent_color" | "reduced_motion" | "animation_intensity" | "orb_style" | "font_size" | "sidebar_hidden">>;

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

export function isHexColor(value: string): boolean {
  return HEX_COLOR.test(value);
}

function readStored(): Partial<UiPreferences> {
  try {
    const raw = typeof localStorage === "undefined" ? null : localStorage.getItem(UI_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return typeof parsed === "object" && parsed !== null ? sanitize(parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** Keeps only known keys with valid values, so a tampered or old localStorage entry cannot break the UI. */
export function sanitize(input: Record<string, unknown>): Partial<UiPreferences> {
  const out: Partial<UiPreferences> = {};
  if (input.theme === "dark" || input.theme === "light" || input.theme === "system" || input.theme === "high-contrast") out.theme = input.theme;
  if (typeof input.accentColor === "string" && isHexColor(input.accentColor)) out.accentColor = input.accentColor;
  if (input.reducedMotion === "system" || input.reducedMotion === "on" || input.reducedMotion === "off") out.reducedMotion = input.reducedMotion;
  if (["off", "low", "normal", "high"].includes(input.animationIntensity as string)) out.animationIntensity = input.animationIntensity as AnimationIntensity;
  if (["orb", "ring", "minimal"].includes(input.orbStyle as string)) out.orbStyle = input.orbStyle as OrbStyle;
  if (["small", "medium", "large"].includes(input.fontSize as string)) out.fontSize = input.fontSize as FontSizeSetting;
  if (Array.isArray(input.hiddenNav) && input.hiddenNav.every((item) => typeof item === "string")) out.hiddenNav = input.hiddenNav as string[];
  if (Array.isArray(input.sidebarOrder) && input.sidebarOrder.every((item) => typeof item === "string")) out.sidebarOrder = input.sidebarOrder as string[];
  return out;
}

function persist(state: UiPreferences): void {
  try {
    if (typeof localStorage === "undefined") return;
    const { navOpen: _navOpen, ...stored } = state;
    localStorage.setItem(UI_STORAGE_KEY, JSON.stringify(stored));
  } catch {
    // Storage can be blocked or full. The preference still applies for this page view.
  }
}

/** Maps store keys to the API appearance patch. Pure. */
export function toAppearancePatch(patch: Partial<UiPreferences>): AppearancePatch {
  const out: AppearancePatch = {};
  if (patch.theme !== undefined) out.theme = patch.theme;
  if (patch.accentColor !== undefined) out.accent_color = patch.accentColor;
  if (patch.reducedMotion !== undefined) out.reduced_motion = patch.reducedMotion;
  if (patch.animationIntensity !== undefined) out.animation_intensity = patch.animationIntensity;
  if (patch.orbStyle !== undefined) out.orb_style = patch.orbStyle;
  if (patch.fontSize !== undefined) out.font_size = patch.fontSize;
  if (patch.hiddenNav !== undefined) out.sidebar_hidden = patch.hiddenNav;
  return out;
}

/** Maps settings.appearance to store preferences. Pure. */
export function fromAppearanceSettings(appearance: AppearanceSettings): Partial<UiPreferences> {
  return {
    theme: appearance.theme,
    accentColor: isHexColor(appearance.accent_color) ? appearance.accent_color : DEFAULT_ACCENT,
    reducedMotion: appearance.reduced_motion,
    animationIntensity: appearance.animation_intensity,
    orbStyle: appearance.orb_style,
    fontSize: appearance.font_size,
    hiddenNav: appearance.sidebar_hidden,
  };
}

export type AppearanceSyncHandler = (patch: AppearancePatch) => void;

export interface UiStore {
  getState: () => UiPreferences;
  subscribe: (listener: () => void) => () => void;
  setPreferences: (patch: Partial<UiPreferences>) => void;
  hydrateFromServer: (appearance: AppearanceSettings) => void;
  setSyncHandler: (handler: AppearanceSyncHandler | null) => void;
}

export function createUiStore(initial: Partial<UiPreferences> = {}): UiStore {
  let state: UiPreferences = { ...DEFAULT_UI_PREFERENCES, ...initial };
  const listeners = new Set<() => void>();
  let syncHandler: AppearanceSyncHandler | null = null;

  const commit = (next: UiPreferences): void => {
    if (next === state) return;
    state = next;
    persist(state);
    for (const listener of listeners) listener();
  };

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    setPreferences(patch) {
      const clean = sanitize(patch as Record<string, unknown>);
      const changed = Object.entries(clean).filter(([key, value]) => state[key as keyof UiPreferences] !== value);
      if (changed.length === 0) return;
      const next = { ...state, ...Object.fromEntries(changed) } as UiPreferences;
      commit(next);
      const appearanceChanges = Object.fromEntries(changed.filter(([key]) => APPEARANCE_KEYS.includes(key as AppearanceKey)));
      if (Object.keys(appearanceChanges).length > 0) syncHandler?.(toAppearancePatch(appearanceChanges as Partial<UiPreferences>));
    },
    hydrateFromServer(appearance) {
      const fromServer = fromAppearanceSettings(appearance);
      const next = { ...state, ...fromServer } as UiPreferences;
      if (APPEARANCE_KEYS.every((key) => next[key] === state[key])) return;
      commit(next);
    },
    setSyncHandler(handler) {
      syncHandler = handler;
    },
  };
}

export const uiStore = createUiStore(readStored());

export function useUiStore<T>(selector: (state: UiPreferences) => T, store: UiStore = uiStore): T {
  return useSyncExternalStore(
    store.subscribe,
    () => selector(store.getState()),
    () => selector(store.getState()),
  );
}
