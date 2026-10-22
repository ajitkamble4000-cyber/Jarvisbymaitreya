import { useSyncExternalStore } from "react";
import { useUiStore } from "../stores/ui-store";
import type { AnimationIntensity } from "../types/api";

/** Subscribes to a CSS media query. Returns false when matchMedia is unavailable (e.g. in tests). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => undefined;
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false),
    () => false,
  );
}

/** True when motion should be reduced: the viewer's setting, or the OS preference under "system". */
export function useReducedMotion(): boolean {
  const setting = useUiStore((state) => state.reducedMotion);
  const systemPrefers = useMediaQuery("(prefers-reduced-motion: reduce)");
  return setting === "on" || (setting === "system" && systemPrefers);
}

/** The orb and decorative motion scale with this value. "off" disables them entirely. */
export function useAnimationIntensity(): AnimationIntensity {
  return useUiStore((state) => state.animationIntensity);
}
