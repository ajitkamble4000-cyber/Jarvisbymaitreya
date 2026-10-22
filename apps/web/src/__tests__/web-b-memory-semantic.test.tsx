import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "../pages/memory/MemoryPage";
import { fallbackReasonText, reindexErrorText, reindexSuccessText } from "../pages/memory/memoryModel";
import type { MemoryItem, MemoryListResponse } from "../pages/memory/memoryQueries";
import { ApiError, api } from "../services/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const memory: MemoryItem = {
  id: "11111111-1111-1111-1111-111111111111",
  category: "preference",
  content: "Prefers strong coffee in the morning.",
  source: "manual",
  created_at: "2026-10-19T04:00:00Z",
  updated_at: "2026-10-19T04:00:00Z",
  expires_at: null,
};

function listResponse(overrides: Partial<MemoryListResponse> = {}): MemoryListResponse {
  return {
    items: [memory],
    total: 1,
    enabled: true,
    search_mode: "keyword",
    fallback_reason: null,
    ...overrides,
  };
}

const settingsDoc = { memory: { enabled: true, retention_days: null, auto_save_summaries: false } };

describe("Memory page: search by meaning", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends semantic=true only while the switch is on, and shows the search mode as text", async () => {
    const { calls } = routeFetch({
      "/memories": () => listResponse(),
      "/settings": () => settingsDoc,
    });
    const user = userEvent.setup();
    renderWithProviders(<MemoryPage />, { route: "/memory" });

    expect(await screen.findByText("Search mode: keyword.")).toBeInTheDocument();
    expect(calls.some((call) => call.includes("semantic="))).toBe(false);
    expect(screen.queryByRole("button", { name: "Build search index" })).toBeNull();

    const toggle = screen.getByRole("switch", { name: "Search by meaning" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(calls.some((call) => call.startsWith("/memories") && call.includes("semantic=true"))).toBe(true));
    expect(screen.getByRole("button", { name: "Build search index" })).toBeInTheDocument();
  });

  it("names the fallback reason and its meaning when keyword search answered a semantic request", async () => {
    routeFetch({
      "/memories": () => listResponse({ search_mode: "keyword", fallback_reason: "no_embedding_model" }),
      "/settings": () => settingsDoc,
    });
    const user = userEvent.setup();
    renderWithProviders(<MemoryPage />, { route: "/memory" });

    await screen.findByText("Search mode: keyword.");
    await user.click(screen.getByRole("switch", { name: "Search by meaning" }));

    expect(await screen.findByText("Fallback reason: no_embedding_model. Semantic search is not set up on the server.")).toBeInTheDocument();
  });

  it("reports a semantic result without a fallback line", async () => {
    routeFetch({
      // The API ranks by meaning only when semantic=true is sent, so the fixture does the same.
      "/memories": (url) => listResponse({ search_mode: url.searchParams.get("semantic") === "true" ? "semantic" : "keyword", fallback_reason: null }),
      "/settings": () => settingsDoc,
    });
    const user = userEvent.setup();
    renderWithProviders(<MemoryPage />, { route: "/memory" });

    await screen.findByText("Search mode: keyword.");
    await user.click(screen.getByRole("switch", { name: "Search by meaning" }));

    expect(await screen.findByText("Search mode: semantic.")).toBeInTheDocument();
    expect(screen.queryByText(/Fallback reason/)).toBeNull();
  });

  it("builds the search index and reports updated and remaining", async () => {
    const { mock } = routeFetch({
      "/memories": () => listResponse(),
      "/memories/reindex": () => ({ updated: 200, remaining: 5 }),
      "/settings": () => settingsDoc,
    });
    const user = userEvent.setup();
    renderWithProviders(<MemoryPage />, { route: "/memory" });

    await screen.findByText("Search mode: keyword.");
    await user.click(screen.getByRole("switch", { name: "Search by meaning" }));
    await user.click(await screen.findByRole("button", { name: "Build search index" }));

    // The same sentence is shown inline and in the toast, so more than one match is expected.
    const shown = await screen.findAllByText("Indexed 200 memories. 5 memories still need an index. Build the index again to continue.");
    expect(shown.length).toBeGreaterThanOrEqual(1);
    const post = mock.mock.calls.find(([path]) => String(path).endsWith("/memories/reindex"));
    expect((post?.[1] as RequestInit | undefined)?.method).toBe("POST");
  });

  it("explains a failed index build with the reason and the counts saved before the failure", async () => {
    routeFetch({
      "/memories": () => listResponse(),
      "/settings": () => settingsDoc,
    });
    vi.spyOn(api, "post").mockRejectedValue(
      new ApiError({
        status: 409,
        code: "embedding_unavailable",
        message: "The embedding model is not available.",
        details: { reason: "provider_not_configured", updated: 100, remaining: 12 },
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MemoryPage />, { route: "/memory" });

    await screen.findByText("Search mode: keyword.");
    await user.click(screen.getByRole("switch", { name: "Search by meaning" }));
    await user.click(await screen.findByRole("button", { name: "Build search index" }));

    // The inline note is a paragraph with role alert. The toast repeats the sentence, so select by element.
    const text = "Add a Google Gemini key in AI Models, then build the index again. Saved so far: 100. Still without an index: 12.";
    const matches = await screen.findAllByText(text);
    const note = matches.find((element) => element.tagName === "P" && element.getAttribute("role") === "alert");
    expect(note).toBeDefined();
    expect(within(note as HTMLElement).getByText(text)).toBeInTheDocument();
  });
});

describe("Memory search messages", () => {
  it("maps each fallback reason to a sentence and gives unknown reasons a generic one", () => {
    expect(fallbackReasonText(null)).toBeNull();
    expect(fallbackReasonText("no_query")).toBe("Type a phrase to search by meaning.");
    expect(fallbackReasonText("provider_unavailable")).toBe("The embedding service could not be reached. Try again later.");
    expect(fallbackReasonText("something_new")).toBe("Keyword search was used instead.");
  });

  it("describes the reindex result, including the case where nothing is left to index", () => {
    expect(reindexSuccessText({ updated: 200, remaining: 0 })).toBe("Indexed 200 memories. Every memory now has a search index.");
    expect(reindexSuccessText({ updated: 1, remaining: 0 })).toBe("Indexed 1 memory. Every memory now has a search index.");
    expect(reindexSuccessText({ updated: 0, remaining: 0 })).toBe("Every memory already has a search index.");
    expect(reindexSuccessText({ updated: 200, remaining: 1 })).toBe("Indexed 200 memories. 1 memory still needs an index. Build the index again to continue.");
  });

  it("reads the reindex error details and falls back when they are missing", () => {
    expect(reindexErrorText({ reason: "no_embedding_model", updated: 0, remaining: 3 })).toBe(
      "Semantic search is not set up on the server, so no index was built. Saved so far: 0. Still without an index: 3.",
    );
    expect(reindexErrorText({ reason: "provider_unavailable", updated: 200, remaining: 4 })).toBe(
      "The embedding service could not be reached. Try again later. Saved so far: 200. Still without an index: 4.",
    );
    expect(reindexErrorText(null)).toBe("The search index could not be built. Try again later.");
    expect(reindexErrorText({ reason: "surprise" })).toBe("The search index could not be built.");
  });
});
