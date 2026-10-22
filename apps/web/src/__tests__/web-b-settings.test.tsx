import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiSection } from "../pages/settings/AiSection";
import { VoiceSection } from "../pages/settings/VoiceSection";
import { changedKeys, validateAiFields } from "../pages/settings/settingsModel";
import { api } from "../services/api";
import type { SettingsDoc } from "../types/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const settings: SettingsDoc = {
  ai: {
    active_provider: "gemini",
    fallback_provider: null,
    fallback_enabled: false,
    temperature: 0.7,
    max_output_tokens: 2048,
    system_instructions: "",
    stream_responses: true,
    include_memory: true,
    daily_call_limit: null,
  },
  voice: {
    language: "en-US",
    voice_name: null,
    speaking_rate: 1,
    pitch: 1,
    captions_enabled: true,
    auto_speak: true,
    interruptions_enabled: true,
    push_to_talk: true,
    continuous_listening: false,
    wake_word_enabled: false,
    microphone_device_id: null,
    audio_output_device_id: null,
  },
  appearance: {
    theme: "dark",
    accent_color: "#22d3ee",
    orb_style: "orb",
    animation_intensity: "normal",
    reduced_motion: "system",
    font_size: "medium",
    sidebar_hidden: [],
    dashboard_widgets: { status: true, devices: true, tasks: true, activity: true, schedule: true, quick_actions: true },
  },
  automation: { max_concurrent_tasks: 3, default_task_timeout_seconds: 120, disabled_tools: [], emergency_stopped: false },
  notifications: {
    desktop_notifications: false,
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
  },
  memory: { enabled: true, retention_days: null, auto_save_summaries: false },
  privacy: { store_conversations: true },
  timezone: null,
};

const providers = [
  {
    id: "gemini",
    display_name: "Google Gemini",
    enabled: true,
    configured: true,
    credential_hint: "…a1b2",
    selected_model: null,
    timeout_seconds: 30,
    max_retries: 2,
    status: "untested",
    last_tested_at: null,
    last_latency_ms: null,
    last_error: null,
    capabilities: { streaming: true, tool_calling: true, multimodal_input: false, usage_reporting: true, model_listing: true },
  },
];

describe("Settings save", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("the changed-key helper returns only the keys that differ", () => {
    expect(changedKeys({ a: 1, b: "x", c: [1] }, { a: 2, b: "x", c: [1] })).toEqual({ a: 2 });
    expect(changedKeys({ a: 1, b: "x" }, { a: 1, b: "x" })).toEqual({});
  });

  it("the AI form sends only the field that changed", async () => {
    routeFetch({ "/providers": () => ({ items: providers }) });
    const patch = vi.spyOn(api, "patch").mockResolvedValue(settings);
    const user = userEvent.setup();
    renderWithProviders(<AiSection ai={settings.ai} />, { route: "/settings?tab=ai" });

    const temperature = screen.getByLabelText("Temperature");
    await user.clear(temperature);
    await user.type(temperature, "0.5");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith("/settings", { ai: { temperature: 0.5 } });
  });

  it("a switch saves only its own key", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue(settings);
    const user = userEvent.setup();
    renderWithProviders(<VoiceSection voice={settings.voice} />, { route: "/settings?tab=voice" });

    await user.click(screen.getByRole("switch", { name: "Show captions" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith("/settings", { voice: { captions_enabled: false } });
  });

  it("rejects a fallback provider that equals the active one and an out-of-range temperature", () => {
    const errors = validateAiFields({
      temperature: "3",
      maxOutputTokens: "2048",
      systemInstructions: "",
      activeProvider: "gemini",
      fallbackProvider: "gemini",
    });
    expect(errors.temperature).toBe("Enter a temperature from 0 to 2.");
    expect(errors.fallbackProvider).toBe("The fallback must be a different provider from the active one.");
  });
});
