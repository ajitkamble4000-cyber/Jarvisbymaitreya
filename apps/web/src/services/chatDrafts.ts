/**
 * Unsent chat text, kept in this browser so that leaving a conversation does not lose it. Drafts never go to the
 * server. Storage can be blocked or full, and then nothing is kept and the message box works as before.
 */

const PREFIX = "jarvis.chat-draft.";

/** Longer text is not kept as a draft. A very long paste is either sent or lost, never half-stored. */
export const DRAFT_MAX_CHARS = 20000;

export function draftKey(scope: string): string {
  return `${PREFIX}${scope}`;
}

export function readDraft(scope: string): string {
  try {
    return window.localStorage.getItem(draftKey(scope)) ?? "";
  } catch {
    return "";
  }
}

/** Saves the text. Blank text, or text longer than DRAFT_MAX_CHARS, removes the draft instead. */
export function writeDraft(scope: string, text: string): void {
  try {
    if (text.trim() === "" || text.length > DRAFT_MAX_CHARS) {
      window.localStorage.removeItem(draftKey(scope));
    } else {
      window.localStorage.setItem(draftKey(scope), text);
    }
  } catch {
    // Storage is blocked or full. The box still works; the draft is simply not kept.
  }
}

/** Removes every chat draft from this browser. Called on sign out, so a shared computer keeps no unsent text. */
export function clearChatDrafts(): void {
  try {
    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith(PREFIX)) keys.push(key);
    }
    for (const key of keys) window.localStorage.removeItem(key);
  } catch {
    // Storage is unavailable, so there is nothing to clear.
  }
}
