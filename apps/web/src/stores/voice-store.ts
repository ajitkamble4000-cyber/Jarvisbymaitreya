/**
 * Voice state machine. Every change follows a confirmed browser event:
 *   - recognitionActive is true only after the recognition engine reports that it started.
 *   - speaking is shown only after the synthesis engine reports that an utterance started.
 * The reducer is pure so the transitions can be tested without a browser.
 */

import { useSyncExternalStore } from "react";

export type VoiceState = "idle" | "listening" | "processing" | "speaking" | "error";

export interface VoiceSnapshot {
  /** Primary activity shown to the viewer. */
  state: VoiceState;
  /** True only between a confirmed recognition start and its end. */
  recognitionActive: boolean;
  /** Message to show when state is "error". Null otherwise. */
  error: string | null;
  /** Last final transcript recognised from the microphone. */
  lastTranscript: string;
  /** Last text spoken by the synthesis engine. */
  lastSpoken: string;
}

export type VoiceEvent =
  | { type: "RECOGNITION_STARTED" }
  | { type: "RECOGNITION_ENDED" }
  | { type: "TRANSCRIPT_FINAL"; text: string }
  | { type: "RESPONSE_DONE" }
  | { type: "SPEAK_STARTED"; text: string }
  | { type: "SPEAK_ENDED" }
  | { type: "INTERRUPTED" }
  | { type: "FAILED"; message: string }
  | { type: "RESET" };

export const INITIAL_VOICE_SNAPSHOT: VoiceSnapshot = {
  state: "idle",
  recognitionActive: false,
  error: null,
  lastTranscript: "",
  lastSpoken: "",
};

/**
 * Transitions:
 *   RECOGNITION_STARTED  recognitionActive=true; idle or error -> listening
 *   RECOGNITION_ENDED    recognitionActive=false; listening -> idle
 *   TRANSCRIPT_FINAL     listening or idle -> processing (blank text is ignored)
 *   RESPONSE_DONE        processing -> idle
 *   SPEAK_STARTED        any state except speaking -> speaking
 *   SPEAK_ENDED          speaking -> listening when recognition is active, otherwise idle
 *   INTERRUPTED          speaking -> listening (the viewer started talking over the reply)
 *   FAILED               any -> error
 *   RESET                any -> idle, error cleared
 * Events that do not apply to the current state return the same snapshot object.
 */
export function voiceReducer(snapshot: VoiceSnapshot, event: VoiceEvent): VoiceSnapshot {
  switch (event.type) {
    case "RECOGNITION_STARTED": {
      const state = snapshot.state === "idle" || snapshot.state === "error" ? "listening" : snapshot.state;
      if (snapshot.recognitionActive && state === snapshot.state && snapshot.error === null) return snapshot;
      return { ...snapshot, recognitionActive: true, state, error: state === "listening" ? null : snapshot.error };
    }

    case "RECOGNITION_ENDED": {
      if (!snapshot.recognitionActive && snapshot.state !== "listening") return snapshot;
      return { ...snapshot, recognitionActive: false, state: snapshot.state === "listening" ? "idle" : snapshot.state };
    }

    case "TRANSCRIPT_FINAL": {
      const text = event.text.trim();
      if (!text) return snapshot;
      if (snapshot.state !== "listening" && snapshot.state !== "idle") return snapshot;
      return { ...snapshot, state: "processing", error: null, lastTranscript: text };
    }

    case "RESPONSE_DONE":
      return snapshot.state === "processing" ? { ...snapshot, state: "idle" } : snapshot;

    case "SPEAK_STARTED": {
      if (snapshot.state === "speaking") return snapshot;
      const spoken = event.text.trim();
      return { ...snapshot, state: "speaking", error: null, lastSpoken: spoken || snapshot.lastSpoken };
    }

    case "SPEAK_ENDED":
      if (snapshot.state !== "speaking") return snapshot;
      return { ...snapshot, state: snapshot.recognitionActive ? "listening" : "idle" };

    case "INTERRUPTED":
      return snapshot.state === "speaking" ? { ...snapshot, state: "listening" } : snapshot;

    case "FAILED":
      return { ...snapshot, state: "error", error: event.message };

    case "RESET":
      return snapshot.state === "idle" && snapshot.error === null ? snapshot : { ...snapshot, state: "idle", error: null };

    default:
      return snapshot;
  }
}

export interface VoiceStore {
  getSnapshot: () => VoiceSnapshot;
  subscribe: (listener: () => void) => () => void;
  dispatch: (event: VoiceEvent) => void;
}

export function createVoiceStore(initial: VoiceSnapshot = INITIAL_VOICE_SNAPSHOT): VoiceStore {
  let snapshot = initial;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    dispatch(event) {
      const next = voiceReducer(snapshot, event);
      if (next === snapshot) return;
      snapshot = next;
      for (const listener of listeners) listener();
    },
  };
}

export const voiceStore = createVoiceStore();

export function useVoiceSnapshot(store: VoiceStore = voiceStore): VoiceSnapshot {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
