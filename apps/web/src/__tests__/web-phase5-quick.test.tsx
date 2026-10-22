import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import QuickAskPage from "../pages/quick/QuickAskPage";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const streamed = vi.hoisted(() => ({
  calls: [] as Array<{ conversationId: string; content: string }>,
}));

vi.mock("../services/chatStream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../services/chatStream")>();
  return {
    ...actual,
    streamConversationMessage: async function* (conversationId: string, body: { content: string }) {
      streamed.calls.push({ conversationId, content: body.content });
      yield { type: "text_delta", message_id: "m2", text: "The calendar is open." } as const;
      yield { type: "done" } as const;
    },
  };
});

const conversation = {
  id: "c-quick",
  title: "open the calendar",
  archived: false,
  pinned: false,
  provider_id: null,
  model: null,
  created_at: "2026-10-22T09:00:00Z",
  updated_at: "2026-10-22T09:00:00Z",
  last_message_at: null,
};

function stubRoutes() {
  return routeFetch({
    "/settings": () => ({ voice: { language: "en-US", voice_name: null, speaking_rate: 1, pitch: 1, wake_word_enabled: false } }),
    "/conversations": () => conversation,
  });
}

afterEach(() => {
  streamed.calls.length = 0;
  delete window.jarvisQuick;
  vi.unstubAllGlobals();
});

describe("quick ask page", () => {
  it("sends a typed request in its own conversation and shows the reply", async () => {
    stubRoutes();
    renderWithProviders(<QuickAskPage />, { route: "/quick", path: "/quick" });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("What should JARVIS do?"), "open the calendar{Enter}");

    expect(await screen.findByText("The calendar is open.")).toBeInTheDocument();
    expect(streamed.calls).toEqual([{ conversationId: "c-quick", content: "open the calendar" }]);
    expect(screen.getByRole("link", { name: "Open in chat" })).toHaveAttribute("href", "/chat/c-quick");
  });

  it("sends a request handed over in the address once, as the title and the message", async () => {
    const { mock } = stubRoutes();
    renderWithProviders(<QuickAskPage />, { route: "/quick?ask=open%20the%20calendar", path: "/quick" });

    expect(await screen.findByText("The calendar is open.")).toBeInTheDocument();
    expect(streamed.calls).toEqual([{ conversationId: "c-quick", content: "open the calendar" }]);
    const creation = mock.mock.calls.find(([input, init]) => String(input).endsWith("/conversations") && init?.method === "POST");
    expect(creation?.[1]?.body).toBe(JSON.stringify({ title: "open the calendar" }));
  });

  it("closes the desktop window on Escape when the bridge is there", async () => {
    stubRoutes();
    const hide = vi.fn();
    window.jarvisQuick = { hide };
    renderWithProviders(<QuickAskPage />, { route: "/quick", path: "/quick" });
    const user = userEvent.setup();

    await user.keyboard("{Escape}");

    expect(hide).toHaveBeenCalledTimes(1);
  });

  it("clears the box on Escape in a browser tab", async () => {
    stubRoutes();
    renderWithProviders(<QuickAskPage />, { route: "/quick", path: "/quick" });
    const user = userEvent.setup();
    const box = screen.getByLabelText("What should JARVIS do?");
    await user.type(box, "half a request");

    await user.keyboard("{Escape}");

    expect(box).toHaveValue("");
  });

  it("does not offer the microphone where the browser cannot listen", () => {
    stubRoutes();
    renderWithProviders(<QuickAskPage />, { route: "/quick", path: "/quick" });

    expect(screen.queryByRole("button", { name: "Speak your request" })).not.toBeInTheDocument();
  });

  it("shows a failure in words when the request cannot be made", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/conversations")) {
          return new Response(JSON.stringify({ error: { code: "not_authenticated", message: "Sign in to continue." } }), {
            status: 401,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ voice: { language: "en-US", voice_name: null, speaking_rate: 1, pitch: 1, wake_word_enabled: false } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    renderWithProviders(<QuickAskPage />, { route: "/quick", path: "/quick" });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("What should JARVIS do?"), "hello{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("Sign in to continue.");
    expect(streamed.calls).toHaveLength(0);
  });
});
