import type { MessageMeta } from "../../types/api";
import type { MessageOut } from "../../types/api";

/** The last user message. Only it can be edited, because editing removes every later message. */
export function lastUserMessageId(messages: readonly MessageOut[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === "user") return message.id;
  }
  return null;
}

/** The last assistant message. Only it offers Regenerate. */
export function lastAssistantMessageId(messages: readonly MessageOut[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === "assistant") return message.id;
  }
  return null;
}

/** Uses the server's order (oldest first) and drops duplicates that a stream may have echoed. */
export function mergeMessages(messages: readonly MessageOut[], extra: readonly (MessageOut | null)[]): MessageOut[] {
  const seen = new Set(messages.map((message) => message.id));
  const merged = [...messages];
  for (const message of extra) {
    if (message && !seen.has(message.id)) {
      seen.add(message.id);
      merged.push(message);
    }
  }
  return merged;
}

export type FeedbackRating = "up" | "down";

/** The rating the viewer gave a reply, or null. Only "up" and "down" count. */
export function feedbackRating(meta: MessageMeta | undefined): FeedbackRating | null {
  const value = meta?.feedback?.rating;
  return value === "up" || value === "down" ? value : null;
}

/** Pressing the rating already given clears it. Pressing the other rating switches to it. */
export function nextFeedback(current: FeedbackRating | null, pressed: FeedbackRating): FeedbackRating | null {
  return current === pressed ? null : pressed;
}

/**
 * The messages whose text contains the query, ignoring case. Blank means no filter, so every message is kept.
 * Tool and system messages are searched too, because they can hold the text a user is looking for.
 */
export function messagesMatching<T extends { content: string }>(messages: readonly T[], query: string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...messages];
  return messages.filter((message) => message.content.toLowerCase().includes(needle));
}
