import { useEffect, useRef } from "react";
import { uiStore, type AppearancePatch } from "../stores/ui-store";
import { useUpdateSettings } from "../services/queries";
import { useToast } from "../components/ui/Toast";
import type { AppearanceSettings } from "../types/api";

/** Delay that merges quick successive changes (for example, dragging the accent colour) into one PATCH. */
export const APPEARANCE_SYNC_DELAY_MS = 400;

/**
 * Keeps appearance in step with the settings API. Server values hydrate the ui store when
 * settings arrive. Viewer changes are sent as a debounced PATCH /settings { appearance }.
 */
export function useAppearanceSync(appearance: AppearanceSettings | undefined): void {
  const update = useUpdateSettings();
  const { toast } = useToast();
  const mutateRef = useRef(update.mutate);
  const toastRef = useRef(toast);

  useEffect(() => {
    mutateRef.current = update.mutate;
  }, [update.mutate]);

  useEffect(() => {
    toastRef.current = toast;
  }, [toast]);

  useEffect(() => {
    if (appearance) uiStore.hydrateFromServer(appearance);
  }, [appearance]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending: AppearancePatch = {};

    const flush = (): void => {
      const patch = pending;
      pending = {};
      mutateRef.current(
        { appearance: patch },
        {
          onError: (error) =>
            toastRef.current({ tone: "error", title: "Appearance was not saved", description: error.message }),
        },
      );
    };

    uiStore.setSyncHandler((patch) => {
      pending = { ...pending, ...patch };
      if (timer !== undefined) clearTimeout(timer);
      timer = setTimeout(flush, APPEARANCE_SYNC_DELAY_MS);
    });

    return () => {
      uiStore.setSyncHandler(null);
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);
}
