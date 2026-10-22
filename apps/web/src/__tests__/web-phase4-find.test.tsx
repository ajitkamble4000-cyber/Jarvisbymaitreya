import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ChatPage from "../pages/chat/ChatPage";
import { messagesMatching } from "../pages/chat/chatModel";
import { renderWithProviders } from "../test/utils";
import type { MessageOut } from "../types/api";
import { routeFetch } from "./helpers";

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

describe("messagesMatching", () => {
  const items = [{ content: "Pack the sunscreen" }, { content: "Book flights" }, { content: "SUNSCREEN in the bag" }];

  it("keeps every message for a blank query and matches case-insensitively otherwise", () => {
    expect(messagesMatching(items, "  ")).toHaveLength(3);
    expect(messagesMatching(items, "sunscreen")).toHaveLength(2);
    expect(messagesMatching(items, "flight")).toEqual([{ content: "Book flights" }]);
    expect(messagesMatching(items, "zzz")).toEqual([]);
  });
});

describe("find in this conversation", () => {
  it("narrows the messages and says how many match", async () => {
    routeFetch({
      "/conversations/c1": () => ({
        conversation: { id: "c1", title: "Holiday", archived: false, pinned: false, provider_id: null, model: null, created_at: "2026-10-21T09:00:00Z", updated_at: "2026-10-21T09:00:00Z", last_message_at: null },
        messages: [message("m1", "user", "Pack the sunscreen"), message("m2", "assistant", "Book the flights"), message("m3", "user", "Sunscreen factor 50")],
      }),
      "/conversations": () => ({ items: [], total: 0 }),
    });
    renderWithProviders(<ChatPage />, { route: "/chat/c1", path: "/chat/:conversationId?" });
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Find in this conversation"), "sunscreen");

    expect(await screen.findByText("2 of 3 messages match.")).toBeInTheDocument();
    const log = screen.getByRole("log", { name: "Messages" });
    expect(log.textContent).toContain("Pack the sunscreen");
    expect(log.textContent).not.toContain("Book the flights");
  });
});
