import { describe, expect, it } from "vitest";
import { chatStreamReducer, INITIAL_CHAT_STREAM_STATE } from "../pages/chat/chatStreamState";
import { mergeMessages, lastAssistantMessageId, lastUserMessageId } from "../pages/chat/chatModel";
import type { MessageOut } from "../types/api";

const message = (id: string, role: MessageOut["role"], content = id): MessageOut => ({
  id,
  conversation_id: "c1",
  role,
  content,
  status: "complete",
  provider_id: null,
  model: null,
  task_id: null,
  meta: {},
  created_at: "2026-10-19T04:00:00Z",
  edited_at: null,
});

describe("chat stream reducer", () => {
  it("accumulates streamed text for one assistant message", () => {
    let state = chatStreamReducer(INITIAL_CHAT_STREAM_STATE, { type: "start", conversationId: "c1", pendingUser: "hi" });
    expect(state.status).toBe("streaming");
    state = chatStreamReducer(state, { type: "event", event: { type: "assistant_start", message_id: "a1", provider_id: "gemini", model: "m" } });
    state = chatStreamReducer(state, { type: "event", event: { type: "text_delta", message_id: "a1", text: "Hel" } });
    state = chatStreamReducer(state, { type: "event", event: { type: "text_delta", message_id: "a1", text: "lo" } });
    expect(state.text).toBe("Hello");
    expect(state.messageId).toBe("a1");
  });

  it("keeps a stopped reply as stopped and clears streamed content once the conversation is refetched", () => {
    let state = chatStreamReducer(INITIAL_CHAT_STREAM_STATE, { type: "start", conversationId: "c1", pendingUser: null });
    state = chatStreamReducer(state, { type: "event", event: { type: "text_delta", message_id: "a1", text: "partial" } });
    state = chatStreamReducer(state, { type: "stopped" });
    state = chatStreamReducer(state, { type: "finished" });
    expect(state.status).toBe("stopped");
    expect(state.text).toBe("");
    expect(state.messageId).toBeNull();
  });

  it("records a failure with its code and never shows success", () => {
    let state = chatStreamReducer(INITIAL_CHAT_STREAM_STATE, { type: "start", conversationId: "c1", pendingUser: null });
    state = chatStreamReducer(state, { type: "failed", error: { code: "not_configured", message: "Add a key." } });
    state = chatStreamReducer(state, { type: "finished" });
    expect(state.status).toBe("failed");
    expect(state.error).toEqual({ code: "not_configured", message: "Add a key." });
  });

  it("collects one proposal per task and uses the final assistant text", () => {
    const task = { id: "t1" } as never;
    let state = chatStreamReducer(INITIAL_CHAT_STREAM_STATE, { type: "start", conversationId: "c1", pendingUser: null });
    const proposed = { type: "tool_proposed", message_id: "a1", task, decision: "awaiting_approval" } as const;
    state = chatStreamReducer(state, { type: "event", event: proposed });
    state = chatStreamReducer(state, { type: "event", event: proposed });
    expect(state.proposals).toHaveLength(1);
    state = chatStreamReducer(state, { type: "event", event: { type: "assistant_done", message: message("a1", "assistant", "Final answer.") } });
    expect(state.text).toBe("Final answer.");
  });
});

describe("chat message helpers", () => {
  const list = [message("u1", "user"), message("a1", "assistant"), message("u2", "user")];

  it("finds the last user and assistant message", () => {
    expect(lastUserMessageId(list)).toBe("u2");
    expect(lastAssistantMessageId(list)).toBe("a1");
    expect(lastUserMessageId([])).toBeNull();
  });

  it("merges optimistic messages without duplicating ids the server already returned", () => {
    const merged = mergeMessages(list, [message("u2", "user"), message("pending", "assistant")]).map((item) => item.id);
    expect(merged).toEqual(["u1", "a1", "u2", "pending"]);
  });
});
