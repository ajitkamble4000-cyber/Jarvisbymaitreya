import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProviderCard } from "../pages/models/ProviderCard";
import { credentialHintText, parseTuning } from "../pages/models/providerModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const provider = {
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
} as const;

describe("AI Models credential form", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("never renders a stored key: the input is empty and only the hint is shown", async () => {
    routeFetch({ "/providers/gemini/models": () => ({ items: [] }) });
    renderWithProviders(<ProviderCard provider={{ ...provider }} />, { route: "/models" });

    const input = screen.getByLabelText("API key");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input).toHaveValue("");
    expect(screen.getByText(/ends in …a1b2/)).toBeInTheDocument();
    expect(document.body.innerHTML).not.toMatch(/AIza|sk-[A-Za-z0-9]{8}/);
  });

  it("saves a typed key with PUT and clears the field afterwards", async () => {
    const { mock } = routeFetch({
      "/providers/gemini/models": () => ({ items: [] }),
      "/providers/gemini/credential": () => ({ ...provider, credential_hint: "…wxyz" }),
      "/providers": () => ({ items: [] }),
    });
    const user = userEvent.setup();
    renderWithProviders(<ProviderCard provider={{ ...provider }} />, { route: "/models" });

    await user.type(screen.getByLabelText("API key"), "AIzaSyExampleKeywxyz");
    await user.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(screen.getByLabelText("API key")).toHaveValue(""));
    const put = mock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(put).toBeDefined();
    expect(String((put?.[1] as RequestInit).body)).toContain('"api_key":"AIzaSyExampleKeywxyz"');
    expect(screen.getByRole("status")).toHaveTextContent("Key saved");
  });

  it("explains a missing key without showing one", () => {
    expect(credentialHintText({ ...provider, configured: false, credential_hint: null } as never)).toBe("No key is stored for this provider.");
    expect(credentialHintText(provider as never)).toContain("never shown");
  });

  it("validates timeout and retries before saving", () => {
    expect(parseTuning({ timeout: "30", retries: "2" })).toEqual({ timeout_seconds: 30, max_retries: 2, errors: {} });
    expect(parseTuning({ timeout: "0", retries: "9" }).errors).toEqual({
      timeout: "Enter a timeout from 1 to 120 seconds.",
      retries: "Enter a whole number of retries from 0 to 5.",
    });
  });
});
