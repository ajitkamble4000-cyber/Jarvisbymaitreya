import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ConversationList } from "../pages/chat/ConversationList";
import type { ConversationOut } from "../types/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

function conversation(overrides: Partial<ConversationOut>): ConversationOut {
  return {
    id: "c1",
    title: "Plans",
    archived: false,
    pinned: false,
    provider_id: null,
    model: null,
    created_at: "2026-10-21T09:00:00Z",
    updated_at: "2026-10-21T09:00:00Z",
    last_message_at: null,
    ...overrides,
  };
}

describe("pinning in the conversation list", () => {
  it("shows a pinned conversation as pinned and offers to unpin it", async () => {
    routeFetch({ "/conversations": () => ({ items: [conversation({ pinned: true })], total: 1 }) });
    renderWithProviders(<ConversationList activeId={undefined} />);

    const toggle = await screen.findByRole("button", { name: "Unpin conversation" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/^Pinned · /)).toBeInTheDocument();
  });

  it("pins an unpinned conversation with one request", async () => {
    const patches: unknown[] = [];
    routeFetch({
      "/conversations": () => ({ items: [conversation({})], total: 1 }),
      "/conversations/c1": (_url, init) => {
        if (init?.method === "PATCH") patches.push(JSON.parse(String(init.body)));
        return conversation({ pinned: true });
      },
    });
    renderWithProviders(<ConversationList activeId={undefined} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Pin conversation" }));

    await waitFor(() => expect(patches).toEqual([{ pinned: true }]));
  });
});
