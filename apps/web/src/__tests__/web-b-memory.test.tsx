import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClearMemoriesDialog } from "../pages/memory/MemoryDialogs";
import { MEMORY_CLEAR_PHRASE, isClearPhraseMatch, validateMemoryContent } from "../pages/memory/memoryModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

describe("Memory bulk clear", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("only matches the exact typed phrase", () => {
    expect(MEMORY_CLEAR_PHRASE).toBe("DELETE ALL MEMORIES");
    expect(isClearPhraseMatch("DELETE ALL MEMORIES")).toBe(true);
    expect(isClearPhraseMatch("delete all memories")).toBe(false);
    expect(isClearPhraseMatch("DELETE ALL MEMORIES ")).toBe(false);
  });

  it("keeps the delete button disabled until the phrase is typed exactly, then sends it", async () => {
    const { mock } = routeFetch({ "/memories": () => ({ deleted: 4 }) });
    const onCleared = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ClearMemoriesDialog open onClose={() => undefined} onCleared={onCleared} />, { route: "/memory" });

    const confirm = screen.getByRole("button", { name: "Delete all memories" });
    const input = screen.getByLabelText("Type DELETE ALL MEMORIES to confirm");
    expect(confirm).toBeDisabled();

    await user.type(input, "DELETE ALL");
    expect(confirm).toBeDisabled();

    await user.clear(input);
    await user.type(input, "delete all memories");
    expect(confirm).toBeDisabled();

    await user.clear(input);
    await user.type(input, "DELETE ALL MEMORIES");
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await vi.waitFor(() => expect(onCleared).toHaveBeenCalledWith(4));
    const del = mock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "DELETE");
    expect(del).toBeDefined();
    expect(String((del?.[1] as RequestInit).body)).toContain('"confirm":"DELETE ALL MEMORIES"');
  });

  it("checks memory text before saving", () => {
    expect(validateMemoryContent("   ")).toBe("Enter what JARVIS should remember.");
    expect(validateMemoryContent("x".repeat(2001))).toBe("Use 2000 characters or fewer.");
    expect(validateMemoryContent("Prefers tea")).toBeNull();
  });
});
