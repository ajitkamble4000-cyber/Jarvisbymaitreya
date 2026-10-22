import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatPage from "../pages/chat/ChatPage";
import { renderWithProviders } from "../test/utils";
import type { MessageOut } from "../types/api";
import { routeFetch } from "./helpers";

/** Where the end of the messages sits in the window. Tests move it to act as a reader who scrolled up or back down. */
let endTop = 0;
const scrollIntoView = vi.fn();

beforeEach(() => {
  endTop = 0;
  scrollIntoView.mockClear();
  Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ top: endTop, bottom: endTop, left: 0, right: 0, width: 0, height: 0, x: 0, y: endTop, toJSON: () => ({}) }) as DOMRect,
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

function message(id: string, role: MessageOut["role"], content: string): MessageOut {
  return {
    id,
    conversation_id: "c1",
    role,
    content,
    status: "complete",
    provider_id: null,
    model: null,
    task_id: null,
    meta: {},
    created_at: "2026-10-21T09:00:00Z",
    edited_at: null,
  };
}

function openConversation() {
  routeFetch({
    "/conversations/c1": () => ({
      conversation: { id: "c1", title: "Holiday", archived: false, pinned: false, provider_id: null, model: null, created_at: "2026-10-21T09:00:00Z", updated_at: "2026-10-21T09:00:00Z", last_message_at: null },
      messages: [message("m1", "user", "Pack the sunscreen"), message("m2", "assistant", "Book the flights")],
    }),
    "/conversations": () => ({ items: [], total: 0 }),
  });
  renderWithProviders(<ChatPage />, { route: "/chat/c1", path: "/chat/:conversationId?" });
}

/** Simulates the reader scrolling: the window fires its scroll event after the layout has moved. */
function scrollWindow(): void {
  act(() => {
    window.dispatchEvent(new Event("scroll"));
  });
}

describe("following a reply", () => {
  it("opens a conversation at its end, without a jump button", async () => {
    openConversation();

    expect(await screen.findByText("Book the flights")).toBeInTheDocument();
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "end" });
    expect(screen.queryByRole("button", { name: "Jump to latest" })).not.toBeInTheDocument();
  });

  it("offers to jump back once the reader has scrolled away from the end", async () => {
    openConversation();
    await screen.findByText("Book the flights");

    endTop = 4000;
    scrollWindow();

    expect(await screen.findByRole("button", { name: "Jump to latest" })).toBeInTheDocument();
  });

  it("returns to the end when asked, and hides the button", async () => {
    openConversation();
    await screen.findByText("Book the flights");
    endTop = 4000;
    scrollWindow();
    const user = userEvent.setup();
    const callsBefore = scrollIntoView.mock.calls.length;

    await user.click(await screen.findByRole("button", { name: "Jump to latest" }));

    expect(scrollIntoView.mock.calls.length).toBeGreaterThan(callsBefore);
    expect(screen.queryByRole("button", { name: "Jump to latest" })).not.toBeInTheDocument();
  });

  it("follows again once the end is back on screen", async () => {
    openConversation();
    await screen.findByText("Book the flights");
    endTop = 4000;
    scrollWindow();
    await screen.findByRole("button", { name: "Jump to latest" });

    endTop = 300;
    scrollWindow();

    expect(screen.queryByRole("button", { name: "Jump to latest" })).not.toBeInTheDocument();
  });
});
