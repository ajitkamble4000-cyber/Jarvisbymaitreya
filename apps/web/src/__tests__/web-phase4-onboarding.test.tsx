import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { OnboardingCard } from "../pages/overview/OnboardingCard";
import { ONBOARDING_DISMISSED_KEY, onboardingComplete, onboardingSteps, readDismissed, writeDismissed } from "../pages/overview/onboarding";
import type { DeviceOut, ProviderOut } from "../types/api";
import { renderWithProviders } from "../test/utils";

afterEach(() => {
  window.localStorage.clear();
});

const provider = (overrides: Partial<ProviderOut>): ProviderOut =>
  ({ id: "gemini", display_name: "Google Gemini", configured: false, selected_model: null, ...overrides }) as ProviderOut;
const device = (pairing_status: "paired" | "revoked"): DeviceOut => ({ id: "d1", pairing_status }) as DeviceOut;

describe("onboarding steps", () => {
  it("marks each step from the data the page already has", () => {
    const steps = onboardingSteps({
      providers: [provider({ configured: true, selected_model: null })],
      devices: [device("revoked")],
      timezone: null,
      conversationCount: 0,
    });
    expect(steps.map((step) => [step.id, step.done])).toEqual([
      ["provider", true],
      ["model", false],
      ["device", false],
      ["timezone", false],
      ["chat", false],
    ]);
    expect(onboardingComplete(steps)).toBe(false);
  });

  it("is complete only when every step is done", () => {
    const steps = onboardingSteps({
      providers: [provider({ configured: true, selected_model: "model-a" })],
      devices: [device("paired")],
      timezone: "Europe/London",
      conversationCount: 2,
    });
    expect(onboardingComplete(steps)).toBe(true);
  });

  it("treats missing data as not done rather than done", () => {
    const steps = onboardingSteps({ providers: undefined, devices: undefined, timezone: undefined, conversationCount: undefined });
    expect(steps.every((step) => !step.done)).toBe(true);
  });

  it("links each step to the place that completes it", () => {
    const steps = onboardingSteps({ providers: [], devices: [], timezone: null, conversationCount: 0 });
    expect(Object.fromEntries(steps.map((step) => [step.id, step.href]))).toEqual({
      provider: "/models",
      model: "/models",
      device: "/devices?pair=1",
      timezone: "/settings?tab=automation",
      chat: "/chat",
    });
  });
});

describe("dismissing the checklist", () => {
  it("remembers a dismissal in this browser and survives storage errors", () => {
    expect(readDismissed(window.localStorage)).toBe(false);
    writeDismissed(true, window.localStorage);
    expect(window.localStorage.getItem(ONBOARDING_DISMISSED_KEY)).toBe("1");
    expect(readDismissed(window.localStorage)).toBe(true);
    writeDismissed(false, window.localStorage);
    expect(readDismissed(window.localStorage)).toBe(false);

    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    } as unknown as Storage;
    expect(readDismissed(broken)).toBe(false);
    expect(() => writeDismissed(true, broken)).not.toThrow();
  });

  it("hides the card after Hide this checklist is pressed", async () => {
    const steps = onboardingSteps({ providers: [], devices: [], timezone: null, conversationCount: 0 });
    renderWithProviders(<OnboardingCard steps={steps} />);
    expect(screen.getByRole("heading", { name: "Get JARVIS ready" })).toBeInTheDocument();
    expect(screen.getByText("0 of 5 steps done.")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Hide this checklist" }));

    expect(screen.queryByRole("heading", { name: "Get JARVIS ready" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(ONBOARDING_DISMISSED_KEY)).toBe("1");
  });

  it("does not render when every step is done", () => {
    const steps = onboardingSteps({
      providers: [provider({ configured: true, selected_model: "m" })],
      devices: [device("paired")],
      timezone: "UTC",
      conversationCount: 1,
    });
    renderWithProviders(<OnboardingCard steps={steps} />);
    expect(screen.queryByRole("heading", { name: "Get JARVIS ready" })).not.toBeInTheDocument();
  });
});
