import type { ChatStreamEvent, ToolProposal } from "../../services/chatStream";
import type { FallbackUsed, MessageOut } from "../../types/api";

export type ChatStreamStatus = "idle" | "streaming" | "stopped" | "failed";

export interface ChatStreamState {
  status: ChatStreamStatus;
  conversationId: string | null;
  /** The text the viewer sent, shown until the server message list includes it. */
  pendingUser: string | null;
  /** The user message as the server returned it, once the stream reports it. */
  optimisticUser: MessageOut | null;
  /** The assistant message being streamed. */
  messageId: string | null;
  text: string;
  providerId: string | null;
  model: string | null;
  fallback: FallbackUsed | null;
  proposals: ToolProposal[];
  error: { code: string; message: string } | null;
}

export const INITIAL_CHAT_STREAM_STATE: ChatStreamState = {
  status: "idle",
  conversationId: null,
  pendingUser: null,
  optimisticUser: null,
  messageId: null,
  text: "",
  providerId: null,
  model: null,
  fallback: null,
  proposals: [],
  error: null,
};

export type ChatStreamAction =
  | { type: "start"; conversationId: string; pendingUser: string | null }
  | { type: "event"; event: ChatStreamEvent }
  | { type: "stopped" }
  | { type: "failed"; error: { code: string; message: string } }
  | { type: "finished" };

/**
 * Stream state for one send or regeneration. Streamed text is kept until the conversation has
 * been refetched (finished), so the final server message replaces it without a gap. Pure.
 */
export function chatStreamReducer(state: ChatStreamState, action: ChatStreamAction): ChatStreamState {
  switch (action.type) {
    case "start":
      return { ...INITIAL_CHAT_STREAM_STATE, status: "streaming", conversationId: action.conversationId, pendingUser: action.pendingUser };

    case "event":
      return applyEvent(state, action.event);

    case "stopped":
      return { ...state, status: "stopped" };

    case "failed":
      return { ...state, status: "failed", error: action.error };

    case "finished":
      return {
        ...state,
        status: state.status === "streaming" ? "idle" : state.status,
        pendingUser: null,
        optimisticUser: null,
        messageId: null,
        text: "",
        proposals: [],
      };

    default:
      return state;
  }
}

function applyEvent(state: ChatStreamState, event: ChatStreamEvent): ChatStreamState {
  switch (event.type) {
    case "user_message":
      return { ...state, optimisticUser: event.message, pendingUser: null };

    case "assistant_start":
      return { ...state, messageId: event.message_id, providerId: event.provider_id, model: event.model, text: "" };

    case "text_delta":
      if (state.messageId !== null && state.messageId !== event.message_id) return state;
      return { ...state, messageId: event.message_id, text: state.text + event.text };

    case "tool_proposed": {
      if (state.proposals.some((proposal) => proposal.task.id === event.task.id)) return state;
      const proposal: ToolProposal = {
        message_id: event.message_id,
        task: event.task,
        decision: event.decision,
        ...(event.reason !== undefined ? { reason: event.reason } : {}),
        ...(event.candidates !== undefined ? { candidates: event.candidates } : {}),
      };
      return { ...state, proposals: [...state.proposals, proposal] };
    }

    case "assistant_done": {
      const finalText = event.message.content;
      return {
        ...state,
        messageId: event.message.id,
        text: finalText || state.text,
        fallback: event.message.meta?.fallback_used ?? state.fallback,
      };
    }

    case "error":
      return { ...state, error: { code: event.code, message: event.message } };

    case "done":
      return state;

    default:
      return state;
  }
}
