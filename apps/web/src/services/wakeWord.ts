/**
 * "Hey JARVIS" detection. Pure, so every rule can be tested without a microphone.
 *
 * The phrase must start with a greeting word ("hey", "hi", "ok" or "okay") followed by "jarvis", so ordinary talk that
 * mentions the name ("tell jarvis later") does not wake the assistant. Whatever follows the phrase in the same
 * utterance is the request. If nothing follows, the next utterance is the request, for a short time.
 */

const WAKE_PATTERN = /(?:^|[\s,.!?;:-])(?:hey|hi|ok|okay)[\s,.!?;:-]+jarvis\b[\s,.!?;:-]*/i;

/** How long, in milliseconds, after "Hey JARVIS" on its own the next utterance is still taken as the request. */
export const WAKE_FOLLOW_UP_MS = 8000;

export interface WakeMatch {
  /** True when the wake phrase was heard. */
  woke: boolean;
  /** The words after the phrase, with their original capitals. Empty when the phrase stood alone. */
  command: string;
}

/** Finds the wake phrase anywhere in an utterance and returns the words after it. */
export function parseWakeWord(transcript: string): WakeMatch {
  const match = WAKE_PATTERN.exec(transcript);
  if (!match) return { woke: false, command: "" };
  const command = transcript.slice(match.index + match[0].length).trim();
  return { woke: true, command };
}

/** What the listener remembers between utterances. `awaitingSince` is set after a bare "Hey JARVIS". */
export interface WakeState {
  awaitingSince: number | null;
}

export const IDLE_WAKE_STATE: WakeState = { awaitingSince: null };

export interface WakeStep {
  state: WakeState;
  /** The request to act on now, or null when nothing is ready. */
  command: string | null;
  /** True when this utterance was the wake phrase itself (so the UI can show that JARVIS is listening). */
  woke: boolean;
}

/**
 * One final transcript from the speech engine. A request is ready when the phrase came with words after it, or when
 * the phrase was heard earlier and this utterance arrives in time as the request.
 */
export function wakeStep(state: WakeState, transcript: string, now: number): WakeStep {
  const text = transcript.trim();
  if (text === "") return { state, command: null, woke: false };

  const match = parseWakeWord(text);
  if (match.woke) {
    if (match.command !== "") return { state: IDLE_WAKE_STATE, command: match.command, woke: true };
    return { state: { awaitingSince: now }, command: null, woke: true };
  }

  const waiting = state.awaitingSince !== null && now - state.awaitingSince <= WAKE_FOLLOW_UP_MS;
  if (waiting) return { state: IDLE_WAKE_STATE, command: text, woke: false };
  return { state: IDLE_WAKE_STATE, command: null, woke: false };
}
