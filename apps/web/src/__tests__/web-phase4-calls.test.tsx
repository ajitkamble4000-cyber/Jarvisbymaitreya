import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiSection } from "../pages/settings/AiSection";
import { callsTodayText } from "../pages/settings/settingsModel";
import { qk } from "../services/queries";
import type { AiSettings } from "../types/api";
import { createTestQueryClient, renderWithProviders } from "../test/utils";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ai: AiSettings = {
  daily_call_limit: 10,
  active_provider: "openai",
  fallback_provider: null,
  fallback_enabled: false,
  temperature: 0.7,
  max_output_tokens: 1024,
  system_instructions: "",
  stream_responses: true,
  include_memory: true,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Answers the calls-today request with the given body (or status), and every other request with an empty provider list. */
function stubCallsToday(today: unknown, status = 200) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    return url.includes("/analytics/calls-today") ? json(today, status) : json({ items: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("callsTodayText", () => {
  it("says how many calls were used when no limit is set, in the singular and the plural", () => {
    expect(callsTodayText({ calls: 0, limit: null })).toBe("Used today: 0 AI calls. No daily limit is set.");
    expect(callsTodayText({ calls: 1, limit: null })).toBe("Used today: 1 AI call. No daily limit is set.");
  });

  it("shows the count against the limit and when it resets", () => {
    expect(callsTodayText({ calls: 3, limit: 10 })).toBe("Used today: 3 of 10 AI calls. The count resets at midnight UTC.");
  });

  it("says plainly when the limit has been reached", () => {
    expect(callsTodayText({ calls: 10, limit: 10 })).toBe(
      "Used today: 10 of 10 AI calls. The limit is reached. Replies resume after midnight UTC.",
    );
  });
});

describe("calls today on the AI settings page", () => {
  it("shows the day's count under the daily call limit", async () => {
    stubCallsToday({ calls: 3, limit: 10, resets_at: "2026-10-22T00:00:00Z" });
    renderWithProviders(<AiSection ai={ai} />);

    expect(await screen.findByText("Used today: 3 of 10 AI calls. The count resets at midnight UTC.")).toBeInTheDocument();
  });

  it("asks the analytics route for today's count", async () => {
    const fetchMock = stubCallsToday({ calls: 0, limit: null, resets_at: "2026-10-22T00:00:00Z" });
    renderWithProviders(<AiSection ai={ai} />);

    await screen.findByText(/Used today: 0 AI calls/);
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain("/api/v1/analytics/calls-today");
  });

  it("leaves the line out, and the form usable, when the count cannot be read", async () => {
    stubCallsToday({ error: { code: "internal", message: "Something went wrong." } }, 500);
    const queryClient = createTestQueryClient();
    renderWithProviders(<AiSection ai={ai} />, { queryClient });

    await waitFor(() => expect(queryClient.getQueryState(qk.callsToday)?.status).toBe("error"));

    expect(screen.queryByText(/Used today/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Daily AI call limit")).toHaveValue(10);
  });
});
