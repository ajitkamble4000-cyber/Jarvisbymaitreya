import { useEffect, type ReactNode } from "react";
import type { ThemeSetting } from "../types/api";
import { DEFAULT_ACCENT, isHexColor, useUiStore } from "../stores/ui-store";

/** Returns black or white text, whichever contrasts better with the accent colour (WCAG luminance). */
export function onAccentColor(hex: string): string {
  if (!isHexColor(hex)) return "#04171d";
  const channel = (offset: number): number => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  return luminance > 0.35 ? "#04171d" : "#ffffff";
}

export function resolveDataTheme(theme: ThemeSetting): string | null {
  return theme === "system" ? null : theme;
}

/**
 * Applies the theme, accent colour, font size and reduced-motion preference to the document.
 * The values come from the ui store, which is hydrated from settings.appearance.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useUiStore((state) => state.theme);
  const accent = useUiStore((state) => state.accentColor);
  const fontSize = useUiStore((state) => state.fontSize);
  const reducedMotion = useUiStore((state) => state.reducedMotion);

  useEffect(() => {
    const root = document.documentElement;
    const resolved = resolveDataTheme(theme);
    if (resolved) root.setAttribute("data-theme", resolved);
    else root.removeAttribute("data-theme");
  }, [theme]);

  useEffect(() => {
    const root = document.documentElement;
    const color = isHexColor(accent) ? accent : DEFAULT_ACCENT;
    root.style.setProperty("--accent", color);
    root.style.setProperty("--on-accent", onAccentColor(color));
  }, [accent]);

  useEffect(() => {
    document.documentElement.setAttribute("data-font-size", fontSize);
  }, [fontSize]);

  useEffect(() => {
    const root = document.documentElement;
    const systemPrefers = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const forced = reducedMotion === "on" || (reducedMotion === "system" && systemPrefers);
    root.setAttribute("data-reduced-motion", forced ? "true" : reducedMotion === "off" ? "false" : "system");
  }, [reducedMotion]);

  return <>{children}</>;
}
