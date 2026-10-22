/**
 * Quick ask: one request typed or spoken into the small /quick window, and the reply it gets. Pure reducers, so the
 * rules can be tested without a server.
 */
import type { ChatStreamEvent, ToolProposal } from "./chatStream";

/** The desktop agent's popup exposes this bridge. In a browser it is missing, and the page behaves as a normal page. */
declare global {
  interface Window {
    jarvisQuick?: { hide: () => void };
  }
}

export const QUICK_TITLE_MAX = 60;

/** The title of the conversation a quick ask opens: its first words, on one line. */
export function quickTitle(text: string): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  if (oneLine === "") return "Quick ask";
  return oneLine.length <= QUICK_TITLE_MAX ? oneLine : `${oneLine.slice(0, QUICK_TITLE_MAX - 1).trimEnd()}…`;
}

export interface QuickReply {
  text: string;
  proposals: ToolProposal[];
  /** The error message from the server or the engine, shown in words. Null when there is none. */
  error: string | null;
  done: boolean;
}

export const EMPTY_QUICK_REPLY: QuickReply = { text: "", proposals: [], error: null, done: false };

/** Folds one stream event into the reply. Text arrives in pieces; the final message, when it comes, is the full text. */
export function reduceQuickReply(reply: QuickReply, event: ChatStreamEvent): QuickReply {
  switch (event.type) {
    case "text_delta":
      return { ...reply, text: reply.text + event.text };
    case "assistant_done":
      return event.message.content ? { ...reply, text: event.message.content } : reply;
    case "tool_proposed":
      return { ...reply, proposals: [...reply.proposals, event] };
    case "error":
      return { ...reply, error: event.message };
    case "done":
      return { ...reply, done: true };
    default:
      return reply;
  }
}
