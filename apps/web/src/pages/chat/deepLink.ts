/**
 * Deep links into the chat composer: /chat?prompt=... puts the text in the composer and never sends it.
 * Pure, so the length cap and the empty case can be tested.
 */

export const PROMPT_MAX_LENGTH = 2000;

export interface PromptFromLink {
  /** Text for the composer. Empty when the link has no usable prompt. */
  text: string;
  /** True when the prompt was longer than the cap and was cut. */
  truncated: boolean;
}

export function promptFromSearch(search: string): PromptFromLink {
  const raw = new URLSearchParams(search).get("prompt") ?? "";
  if (raw.trim() === "") return { text: "", truncated: false };
  const truncated = raw.length > PROMPT_MAX_LENGTH;
  return { text: truncated ? raw.slice(0, PROMPT_MAX_LENGTH) : raw, truncated };
}
