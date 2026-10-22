/**
 * Typed wrappers over the chat SSE endpoints (docs/api.md, "Conversations and chat").
 * Used by the Chat page and the Voice page so that both read the same event shapes.
 */

import { fallbackMessage, streamSSE, type SSEEvent } from "./api";
import type { MessageOut, TaskDecision, TaskOut } from "../types/api";

export interface ToolProposal {
  message_id: string;
  task: TaskOut;
  decision: TaskDecision;
  reason?: string;
  candidates?: unknown[];
}

export type ChatStreamEvent =
  | { type: "user_message"; message: MessageOut }
  | { type: "assistant_start"; message_id: string; provider_id: string | null; model: string | null }
  | { type: "text_delta"; message_id: string; text: string }
  | ({ type: "tool_proposed" } & ToolProposal)
  | { type: "assistant_done"; message: MessageOut }
  | { type: "error"; code: string; message: string; message_id?: string }
  | { type: "done" };

export interface SendMessageBody {
  content: string;
  provider_id?: string;
  model?: string;
}

const CHAT_EVENT_NAMES = new Set([
  "user_message",
  "assistant_start",
  "text_delta",
  "tool_proposed",
  "assistant_done",
  "error",
  "done",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Converts one raw SSE event into a typed chat event. Unknown names and malformed payloads
 * return null so that a single bad event never breaks the stream.
 */
export function parseChatEvent(event: SSEEvent): ChatStreamEvent | null {
  if (!CHAT_EVENT_NAMES.has(event.event)) return null;
  let data: unknown;
  try {
    data = JSON.parse(event.data) as unknown;
  } catch {
    return null;
  }
  if (event.event === "done") return { type: "done" };
  if (!isRecord(data)) return null;

  switch (event.event) {
    case "user_message":
      return isRecord(data.message) ? { type: "user_message", message: data.message as unknown as MessageOut } : null;
    case "assistant_start":
      return typeof data.message_id === "string"
        ? {
            type: "assistant_start",
            message_id: data.message_id,
            provider_id: typeof data.provider_id === "string" ? data.provider_id : null,
            model: typeof data.model === "string" ? data.model : null,
          }
        : null;
    case "text_delta":
      return typeof data.message_id === "string" && typeof data.text === "string"
        ? { type: "text_delta", message_id: data.message_id, text: data.text }
        : null;
    case "tool_proposed":
      if (typeof data.message_id !== "string" || !isRecord(data.task) || typeof data.decision !== "string") return null;
      return {
        type: "tool_proposed",
        message_id: data.message_id,
        task: data.task as unknown as TaskOut,
        decision: data.decision as TaskDecision,
        ...(typeof data.reason === "string" ? { reason: data.reason } : {}),
        ...(Array.isArray(data.candidates) ? { candidates: data.candidates } : {}),
      };
    case "assistant_done":
      return isRecord(data.message) ? { type: "assistant_done", message: data.message as unknown as MessageOut } : null;
    case "error":
      return {
        type: "error",
        code: typeof data.code === "string" ? data.code : "stream_error",
        message: typeof data.message === "string" ? data.message : fallbackMessage(0),
        ...(typeof data.message_id === "string" ? { message_id: data.message_id } : {}),
      };
    default:
      return null;
  }
}

async function* streamChat(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
  for await (const raw of streamSSE(path, body, signal)) {
    const parsed = parseChatEvent(raw);
    if (parsed) yield parsed;
  }
}

/** Sends a user message and streams the assistant reply. */
export function streamConversationMessage(conversationId: string, body: SendMessageBody, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
  return streamChat(`/conversations/${encodeURIComponent(conversationId)}/messages`, body, signal);
}

/** Regenerates the reply for an assistant message, or for the user message it answers. */
export function streamRegeneration(conversationId: string, messageId: string, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
  return streamChat(
    `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/regenerate`,
    undefined,
    signal,
  );
}

/** Collects the assistant text from a sequence of chat events. Pure, so it can be tested. */
export function collectAssistantText(events: ChatStreamEvent[]): string {
  let text = "";
  for (const event of events) {
    if (event.type === "text_delta") text += event.text;
    if (event.type === "assistant_done" && event.message.role === "assistant" && event.message.content) {
      text = event.message.content;
    }
  }
  return text;
}
