import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../hooks/useAuth";
import { Composer } from "../pages/chat/Composer";
import { DRAFT_MAX_CHARS, clearChatDrafts, draftKey, readDraft, writeDraft } from "../services/chatDrafts";
import { renderWithProviders } from "../test/utils";

const LABEL = "Message JARVIS";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function chatDraftKeys(): string[] {
  const keys: string[] = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith("jarvis.chat-draft.")) keys.push(key);
  }
  return keys;
}

describe("chat draft store", () => {
  it("keeps the text for its own conversation only", () => {
    writeDraft("conv-1", "half a thought");

    expect(readDraft("conv-1")).toBe("half a thought");
    expect(readDraft("conv-2")).toBe("");
    expect(window.localStorage.getItem(draftKey("conv-1"))).toBe("half a thought");
  });

  it("removes the draft when the text is blank or too long to keep", () => {
    writeDraft("conv-1", "keep me");
    writeDraft("conv-1", "   ");
    expect(readDraft("conv-1")).toBe("");

    writeDraft("conv-1", "keep me");
    writeDraft("conv-1", "x".repeat(DRAFT_MAX_CHARS + 1));
    expect(readDraft("conv-1")).toBe("");
  });

  it("keeps a draft of exactly the longest size", () => {
    const longest = "x".repeat(DRAFT_MAX_CHARS);
    writeDraft("conv-1", longest);
    expect(readDraft("conv-1")).toBe(longest);
  });

  it("does nothing, without an error, when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "QuotaExceededError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    expect(() => writeDraft("conv-1", "text")).not.toThrow();
    expect(readDraft("conv-1")).toBe("");
  });

  it("clears every chat draft and nothing else", () => {
    writeDraft("conv-1", "one");
    writeDraft("new", "two");
    window.localStorage.setItem("jarvis.settings-cache", "keep");

    clearChatDrafts();

    expect(chatDraftKeys()).toEqual([]);
    expect(window.localStorage.getItem("jarvis.settings-cache")).toBe("keep");
  });
});

describe("message box drafts", () => {
  it("starts from the draft saved for its conversation", () => {
    writeDraft("conv-1", "half a thought");

    renderWithProviders(<Composer disabled={false} streaming={false} onSend={vi.fn()} onStop={vi.fn()} draftScope="conv-1" />);

    expect(screen.getByLabelText(LABEL)).toHaveValue("half a thought");
  });

  it("does not show another conversation's draft", () => {
    writeDraft("conv-2", "not for this one");

    renderWithProviders(<Composer disabled={false} streaming={false} onSend={vi.fn()} onStop={vi.fn()} draftScope="conv-1" />);

    expect(screen.getByLabelText(LABEL)).toHaveValue("");
  });

  it("saves each change, and sending clears the draft", async () => {
    const onSend = vi.fn();
    renderWithProviders(<Composer disabled={false} streaming={false} onSend={onSend} onStop={vi.fn()} draftScope="conv-1" />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(LABEL), "Hello");
    expect(readDraft("conv-1")).toBe("Hello");

    await user.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith("Hello");
    expect(readDraft("conv-1")).toBe("");
  });

  it("uses inserted text in place of an old draft", () => {
    writeDraft("conv-1", "old words");

    renderWithProviders(
      <Composer disabled={false} streaming={false} onSend={vi.fn()} onStop={vi.fn()} draftScope="conv-1" initialValue="from a link" />,
    );

    expect(screen.getByLabelText(LABEL)).toHaveValue("from a link");
  });

  it("keeps nothing when it has no draft scope", async () => {
    renderWithProviders(<Composer disabled={false} streaming={false} onSend={vi.fn()} onStop={vi.fn()} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(LABEL), "private");

    expect(chatDraftKeys()).toEqual([]);
  });
});

describe("sign out", () => {
  it("removes the unsent text from this browser", async () => {
    writeDraft("conv-1", "unsent");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 204 })),
    );
    const { result } = renderHook();

    result.current.logout.mutate();

    await waitFor(() => expect(chatDraftKeys()).toEqual([]));
  });
});

/** Renders the auth hook inside the providers so that its mutation can be called from the test. */
function renderHook() {
  const result: { current: ReturnType<typeof useAuth> } = { current: undefined as unknown as ReturnType<typeof useAuth> };
  function Probe() {
    result.current = useAuth();
    return null;
  }
  renderWithProviders(<Probe />);
  return { result };
}
