/**
 * The first-run checklist on Overview. Each step is derived from data the page already has, so the list never
 * claims a step is done that the server does not show. Dismissing it is a per-browser preference kept in
 * localStorage; a failed read or write is ignored, so the checklist simply shows again.
 */

import type { DeviceOut, ProviderOut } from "../../types/api";

export interface OnboardingInput {
  providers: readonly ProviderOut[] | undefined;
  devices: readonly DeviceOut[] | undefined;
  timezone: string | null | undefined;
  conversationCount: number | undefined;
}

export interface OnboardingStep {
  id: "provider" | "model" | "device" | "timezone" | "chat";
  label: string;
  done: boolean;
  href: string;
}

export const ONBOARDING_DISMISSED_KEY = "jarvis.onboarding.dismissed";

export function onboardingSteps(input: OnboardingInput): OnboardingStep[] {
  const providers = input.providers ?? [];
  return [
    {
      id: "provider",
      label: "Add an AI provider key",
      done: providers.some((provider) => provider.configured),
      href: "/models",
    },
    {
      id: "model",
      label: "Choose a model",
      done: providers.some((provider) => provider.configured && Boolean(provider.selected_model)),
      href: "/models",
    },
    {
      id: "device",
      label: "Pair a desktop agent",
      done: (input.devices ?? []).some((device) => device.pairing_status === "paired"),
      href: "/devices?pair=1",
    },
    {
      id: "timezone",
      label: "Set your time zone for reminders",
      done: Boolean(input.timezone),
      href: "/settings?tab=automation",
    },
    {
      id: "chat",
      label: "Send your first message",
      done: (input.conversationCount ?? 0) > 0,
      href: "/chat",
    },
  ];
}

export function onboardingComplete(steps: readonly OnboardingStep[]): boolean {
  return steps.every((step) => step.done);
}

/** Reads the dismissed flag. Storage that is missing or throws counts as not dismissed. */
export function readDismissed(storage: Storage | null | undefined = safeStorage()): boolean {
  try {
    return storage?.getItem(ONBOARDING_DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeDismissed(dismissed: boolean, storage: Storage | null | undefined = safeStorage()): void {
  try {
    if (dismissed) storage?.setItem(ONBOARDING_DISMISSED_KEY, "1");
    else storage?.removeItem(ONBOARDING_DISMISSED_KEY);
  } catch {
    // Private browsing or blocked storage: the checklist shows again next time, which is harmless.
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
