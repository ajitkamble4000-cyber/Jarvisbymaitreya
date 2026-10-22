import { useEffect, useRef, useState } from "react";
import { IDLE_WAKE_STATE, WAKE_FOLLOW_UP_MS, wakeStep, type WakeState } from "../services/wakeWord";
import {
  createRecognitionSession,
  getRecognitionConstructor,
  recognitionErrorMessage,
  type SpeechRecognitionLike,
} from "../services/speech";

export type WakeWordStatus = "off" | "listening" | "heard" | "unsupported" | "blocked" | "error";

export interface WakeWordOptions {
  /** settings.voice.wake_word_enabled. Nothing listens while this is false. */
  enabled: boolean;
  /** True while another part of the app uses the microphone, so two engines do not compete. */
  paused?: boolean;
  lang: string;
  /** Receives the request that followed "Hey JARVIS". */
  onCommand: (command: string) => void;
}

export interface WakeWordController {
  status: WakeWordStatus;
  /** A sentence for the person when the status needs explaining. Null otherwise. */
  message: string | null;
}

/** Consecutive engine failures after which listening stops, so a broken engine does not restart in a loop. */
const MAX_FAILURES = 3;
const RESTART_DELAY_MS = 300;

/**
 * Listens for "Hey JARVIS" in the background. The browser's engine listens for one utterance at a time, so a new
 * session starts after each one ends. The listener stops itself when the microphone is refused, when the speech
 * service cannot be reached, or after repeated failures, and says so in words.
 */
export function useWakeWord({ enabled, paused = false, lang, onCommand }: WakeWordOptions): WakeWordController {
  const [status, setStatus] = useState<WakeWordStatus>("off");
  const [message, setMessage] = useState<string | null>(null);
  const onCommandRef = useRef(onCommand);
  useEffect(() => {
    onCommandRef.current = onCommand;
  }, [onCommand]);

  useEffect(() => {
    if (!enabled || paused) {
      setStatus("off");
      setMessage(null);
      return undefined;
    }
    if (!getRecognitionConstructor()) {
      setStatus("unsupported");
      setMessage("This browser cannot listen for “Hey JARVIS”. Use Chrome or Edge, or type the request.");
      return undefined;
    }

    let active = true;
    let session: SpeechRecognitionLike | null = null;
    let failures = 0;
    let state: WakeState = IDLE_WAKE_STATE;
    const timers = new Set<number>();
    const later = (callback: () => void, delay: number): void => {
      const id = window.setTimeout(() => {
        timers.delete(id);
        callback();
      }, delay);
      timers.add(id);
    };
    const stopWith = (next: WakeWordStatus, text: string | null): void => {
      active = false;
      setStatus(next);
      setMessage(text);
    };

    const start = (): void => {
      if (!active) return;
      const next = createRecognitionSession(lang, {
        onStart: () => {
          failures = 0;
          setStatus((current) => (current === "heard" ? current : "listening"));
          setMessage(null);
        },
        onSpeechStart: () => undefined,
        onInterim: () => undefined,
        onFinal: (text) => {
          const step = wakeStep(state, text, Date.now());
          state = step.state;
          if (step.woke) {
            setStatus("heard");
            // The request may come later: after the follow-up window, the phrase is forgotten.
            later(() => setStatus((current) => (current === "heard" && state.awaitingSince === null ? "listening" : current)), WAKE_FOLLOW_UP_MS);
          }
          if (step.command !== null) {
            setStatus("listening");
            onCommandRef.current(step.command);
          }
        },
        onError: (code) => {
          if (code === "no-speech" || code === "aborted") return;
          if (code === "not-allowed" || code === "service-not-allowed") {
            stopWith("blocked", "The microphone is blocked for this site. Allow it in the browser's site settings to use “Hey JARVIS”.");
            return;
          }
          if (code === "network") {
            stopWith("error", "The browser's speech service could not be reached, so “Hey JARVIS” is off. Chrome and Edge use it over the internet.");
            return;
          }
          failures += 1;
          if (failures >= MAX_FAILURES) stopWith("error", recognitionErrorMessage(code));
        },
        onEnd: () => {
          session = null;
          if (active) later(() => (active && session === null ? start() : undefined), RESTART_DELAY_MS);
        },
      });
      if (!next) {
        stopWith("unsupported", "This browser cannot listen for “Hey JARVIS”. Use Chrome or Edge, or type the request.");
        return;
      }
      session = next;
      try {
        next.start();
      } catch {
        session = null;
        stopWith("error", "The microphone could not be started for “Hey JARVIS”.");
      }
    };

    start();
    return () => {
      active = false;
      timers.forEach((id) => window.clearTimeout(id));
      timers.clear();
      session?.abort();
      session = null;
    };
  }, [enabled, paused, lang]);

  return { status, message };
}
