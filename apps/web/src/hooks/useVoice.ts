import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  configureUtterance,
  createRecognitionSession,
  detectVoiceCapabilities,
  recognitionErrorMessage,
  type SpeechRecognitionLike,
  type VoiceCapabilities,
} from "../services/speech";
import { voiceStore, useVoiceSnapshot, type VoiceSnapshot } from "../stores/voice-store";

export interface UseVoiceOptions {
  /** BCP 47 language tag from settings.voice.language. */
  lang: string;
  voiceName: string | null;
  speakingRate: number;
  pitch: number;
  /** settings.voice.continuous_listening. While recognition runs, it restarts after each utterance. */
  continuous?: boolean;
  /** settings.voice.interruptions_enabled. When true, the viewer starting to speak stops a spoken reply. */
  interruptions?: boolean;
  /** Receives each final transcript. The caller sends it through the chat endpoint. */
  onFinalTranscript?: (text: string) => void;
}

export interface VoiceController {
  capabilities: VoiceCapabilities;
  snapshot: VoiceSnapshot;
  /** Words recognised so far in the current utterance, not yet final. */
  interim: string;
  voices: SpeechSynthesisVoice[];
  /** The continuous setting as the hook applies it. */
  continuous: boolean;
  startListening: () => boolean;
  stopListening: () => void;
  speak: (text: string) => boolean;
  cancelSpeech: () => void;
}


/**
 * Browser voice input and output. Capability detection decides what is offered. The shared
 * voice store records only confirmed engine events, so "listening" and "speaking" are never
 * shown before the browser has started them.
 */
export function useVoice(options: UseVoiceOptions): VoiceController {
  const capabilities = useMemo(() => detectVoiceCapabilities(), []);
  const snapshot = useVoiceSnapshot(voiceStore);
  const [interim, setInterim] = useState("");
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const wantListeningRef = useRef(false);
  const continuousRef = useRef(false);
  const interruptionsRef = useRef(true);
  const optionsRef = useRef(options);
  const continuous = Boolean(options.continuous);

  useEffect(() => {
    optionsRef.current = options;
    continuousRef.current = Boolean(options.continuous);
    interruptionsRef.current = options.interruptions ?? true;
  }, [options]);

  useEffect(() => {
    if (!capabilities.voices || typeof window === "undefined") return undefined;
    const synth = window.speechSynthesis;
    const load = (): void => setVoices(synth.getVoices());
    load();
    synth.addEventListener?.("voiceschanged", load);
    return () => synth.removeEventListener?.("voiceschanged", load);
  }, [capabilities.voices]);

  const startListening = useCallback((): boolean => {
    if (!capabilities.recognition) {
      voiceStore.dispatch({ type: "FAILED", message: "Speech recognition is not supported in this browser. Use the text box instead." });
      return false;
    }
    if (recognitionRef.current) return true;

    const session = createRecognitionSession(optionsRef.current.lang, {
      onStart: () => voiceStore.dispatch({ type: "RECOGNITION_STARTED" }),
      onSpeechStart: () => {
        // Barge-in: the viewer started talking while a reply was playing, so stop the reply when the setting allows it.
        if (interruptionsRef.current && voiceStore.getSnapshot().state === "speaking") {
          window.speechSynthesis?.cancel();
          voiceStore.dispatch({ type: "INTERRUPTED" });
        }
      },
      onInterim: (text) => setInterim(text),
      onFinal: (text) => {
        setInterim("");
        voiceStore.dispatch({ type: "TRANSCRIPT_FINAL", text });
        optionsRef.current.onFinalTranscript?.(text);
      },
      onError: (code) => {
        if (code === "no-speech" || code === "aborted") return;
        wantListeningRef.current = false;
        voiceStore.dispatch({ type: "FAILED", message: recognitionErrorMessage(code) });
      },
      onEnd: () => {
        recognitionRef.current = null;
        setInterim("");
        voiceStore.dispatch({ type: "RECOGNITION_ENDED" });
        if (continuousRef.current && wantListeningRef.current) {
          // Continuous mode restarts the engine after each utterance. The timer avoids a tight loop.
          window.setTimeout(() => {
            if (continuousRef.current && wantListeningRef.current && !recognitionRef.current) startSessionRef.current();
          }, 250);
        }
      },
    });
    if (!session) return false;

    recognitionRef.current = session;
    wantListeningRef.current = true;
    try {
      session.start();
      return true;
    } catch (error) {
      recognitionRef.current = null;
      wantListeningRef.current = false;
      const reason = error instanceof Error ? error.message : "unknown error";
      voiceStore.dispatch({ type: "FAILED", message: `The microphone could not be started (${reason}).` });
      return false;
    }
  }, [capabilities.recognition]);

  // Holds the latest start function so the restart timer always calls the current closure.
  const startSessionRef = useRef(startListening);
  useEffect(() => {
    startSessionRef.current = startListening;
  }, [startListening]);

  const stopListening = useCallback((): void => {
    wantListeningRef.current = false;
    recognitionRef.current?.stop();
  }, []);

  const cancelSpeech = useCallback((): void => {
    if (typeof window !== "undefined") window.speechSynthesis?.cancel();
  }, []);

  const speak = useCallback(
    (text: string): boolean => {
      if (!capabilities.synthesis || typeof window === "undefined" || !window.speechSynthesis) return false;
      const { lang, voiceName, speakingRate, pitch } = optionsRef.current;
      const utterance = configureUtterance(new SpeechSynthesisUtterance(text), { lang, voiceName, speakingRate, pitch }, window.speechSynthesis.getVoices());
      utterance.onstart = () => voiceStore.dispatch({ type: "SPEAK_STARTED", text });
      utterance.onend = () => voiceStore.dispatch({ type: "SPEAK_ENDED" });
      utterance.onerror = (event) => {
        if (event.error === "canceled" || event.error === "interrupted") {
          voiceStore.dispatch({ type: "SPEAK_ENDED" });
          return;
        }
        voiceStore.dispatch({ type: "FAILED", message: `Speech output failed (${event.error}). The text reply is still shown.` });
      };
      window.speechSynthesis.speak(utterance);
      return true;
    },
    [capabilities.synthesis],
  );

  useEffect(() => {
    return () => {
      wantListeningRef.current = false;
      continuousRef.current = false;
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    };
  }, []);

  return {
    capabilities,
    snapshot,
    interim,
    voices,
    continuous,
    startListening,
    stopListening,
    speak,
    cancelSpeech,
  };
}
