import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "../pages/chat/MessageBubble";
import { feedbackRating, nextFeedback } from "../pages/chat/chatModel";
import type { MessageMeta, MessageOut } from "../types/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

function message(overrides: Partial<MessageOut> = {}): MessageOut {
  return {
    id: "m1",
    conversation_id: "c1",
    role: "assistant",
    content: "Here is the plan.",
    status: "complete",
    provider_id: null,
    model: null,
    task_id: null,
    meta: {},
    created_at: "2026-10-21T09:00:00Z",
    edited_at: null,
    ...overrides,
  };
}

describe("feedback rules", () => {
  it("reads only up or down", () => {
    expect(feedbackRating({ feedback: { rating: "up", given_at: "x" } })).toBe("up");
    expect(feedbackRating({ feedback: { rating: "down", given_at: "x" } })).toBe("down");
    expect(feedbackRating({})).toBeNull();
    expect(feedbackRating(undefined)).toBeNull();
    expect(feedbackRating({ feedback: { rating: "maybe" as "up", given_at: "x" } } as MessageMeta)).toBeNull();
  });

  it("clears the same rating and switches to the other", () => {
    expect(nextFeedback(null, "up")).toBe("up");
    expect(nextFeedback("up", "up")).toBeNull();
    expect(nextFeedback("up", "down")).toBe("down");
    expect(nextFeedback("down", "down")).toBeNull();
  });
});

describe("feedback buttons", () => {
  it("shows the rating already given as pressed", () => {
    renderWithProviders(<MessageBubble message={message({ meta: { feedback: { rating: "up", given_at: "x" } } })} canEdit={false} canRegenerate={false} disabled={false} onEdit={() => {}} onRegenerate={() => {}} />);
    expect(screen.getByRole("button", { name: "Helpful reply" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "false");
  });

  it("stores a rating when pressed and clears it when the same one is pressed again", async () => {
    const bodies: unknown[] = [];
    routeFetch({
      "/conversations/c1/messages/m1/feedback": (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return message();
      },
    });
    renderWithProviders(<MessageBubble message={message()} canEdit={false} canRegenerate={false} disabled={false} onEdit={() => {}} onRegenerate={() => {}} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    await waitFor(() => expect(bodies).toEqual([{ rating: "down" }]));
  });

  it("clears a rating that is already given", async () => {
    const bodies: unknown[] = [];
    routeFetch({
      "/conversations/c1/messages/m1/feedback": (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return message();
      },
    });
    renderWithProviders(<MessageBubble message={message({ meta: { feedback: { rating: "up", given_at: "x" } } })} canEdit={false} canRegenerate={false} disabled={false} onEdit={() => {}} onRegenerate={() => {}} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Helpful reply" }));
    await waitFor(() => expect(bodies).toEqual([{ rating: null }]));
  });

  it("is not offered on the viewer's own messages or on a reply still streaming", () => {
    const { unmount } = renderWithProviders(<MessageBubble message={message({ role: "user", content: "Question" })} canEdit={false} canRegenerate={false} disabled={false} onEdit={() => {}} onRegenerate={() => {}} />);
    expect(screen.queryByRole("button", { name: "Helpful reply" })).not.toBeInTheDocument();
    unmount();
    renderWithProviders(<MessageBubble message={message({ status: "streaming" })} canEdit={false} canRegenerate={false} disabled={false} onEdit={() => {}} onRegenerate={() => {}} />);
    expect(screen.queryByRole("button", { name: "Not helpful" })).not.toBeInTheDocument();
  });
});
