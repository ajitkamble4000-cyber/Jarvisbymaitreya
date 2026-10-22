import { describe, expect, it } from "vitest";
import type { ChatStreamEvent } from "../services/chatStream";
import { EMPTY_QUICK_REPLY, QUICK_TITLE_MAX, quickTitle, reduceQuickReply } from "../services/quickAsk";
import type { MessageOut, TaskOut } from "../types/api";

const task = { id: "t1", title: "Open Chrome", tool_name: "launch_app", status: "awaiting_approval" } as unknown as TaskOut;

describe("quickTitle", () => {
  it("uses the request on one line", () => {
    expect(quickTitle("  open   Chrome\non my laptop ")).toBe("open Chrome on my laptop");
  });

  it("shortens a long request with an ellipsis", () => {
    const title = quickTitle("x".repeat(200));
    expect(title.length).toBe(QUICK_TITLE_MAX);
    expect(title.endsWith("…")).toBe(true);
  });

  it("names a blank request", () => {
    expect(quickTitle("   ")).toBe("Quick ask");
  });
});

describe("reduceQuickReply", () => {
  it("joins the streamed pieces in order", () => {
    const events: ChatStreamEvent[] = [
      { type: "text_delta", message_id: "m", text: "Opening " },
      { type: "text_delta", message_id: "m", text: "Chrome." },
    ];
    const reply = events.reduce(reduceQuickReply, EMPTY_QUICK_REPLY);
    expect(reply.text).toBe("Opening Chrome.");
    expect(reply.done).toBe(false);
  });

  it("takes the final message as the full text when it has one", () => {
    const message = { content: "Done: Chrome is open." } as MessageOut;
    const reply = reduceQuickReply({ ...EMPTY_QUICK_REPLY, text: "Done: Chrome" }, { type: "assistant_done", message });
    expect(reply.text).toBe("Done: Chrome is open.");
  });

  it("keeps the streamed text when the final message is empty", () => {
    const message = { content: "" } as MessageOut;
    const reply = reduceQuickReply({ ...EMPTY_QUICK_REPLY, text: "streamed" }, { type: "assistant_done", message });
    expect(reply.text).toBe("streamed");
  });

  it("collects proposals for approval", () => {
    const proposal = { type: "tool_proposed", message_id: "m", task, decision: "needs_approval" } as unknown as ChatStreamEvent;
    const reply = reduceQuickReply(EMPTY_QUICK_REPLY, proposal);
    expect(reply.proposals).toHaveLength(1);
    expect(reply.proposals[0]?.task.id).toBe("t1");
  });

  it("shows an error in words and marks the reply finished at done", () => {
    const withError = reduceQuickReply(EMPTY_QUICK_REPLY, { type: "error", code: "daily_call_limit_reached", message: "The limit is reached." });
    expect(withError.error).toBe("The limit is reached.");
    expect(reduceQuickReply(withError, { type: "done" }).done).toBe(true);
  });
});
