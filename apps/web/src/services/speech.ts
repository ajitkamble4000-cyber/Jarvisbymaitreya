/**
 * Browser speech helpers. Capability detection reads the given scope object, so tests can
 * simulate browsers with or without support. Nothing here fakes a feature the browser lacks.
 */

export interface SpeechRecognitionAlternativeLike {
  transcript: string;
  confidence: number;
}

export interface SpeechRecognitionResultLike {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
}

export interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}

export interface SpeechRecognitionErrorLike {
  error: string;
}

export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onspeechstart: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

export type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

export interface VoiceCapabilities {
  /** The browser exposes SpeechRecognition (or the webkit prefix). */
  recognition: boolean;
  /** speechSynthesis and SpeechSynthesisUtterance exist. */
  synthesis: boolean;
  /** speechSynthesis.getVoices exists, so voice selection can be offered. */
  voices: boolean;
  /** navigator.mediaDevices.enumerateDevices exists, so microphone selection can be offered. */
  microphoneList: boolean;
}

interface SpeechScope {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
  speechSynthesis?: SpeechSynthesis;
  SpeechSynthesisUtterance?: unknown;
  navigator?: { mediaDevices?: { enumerateDevices?: unknown } };
}

export function getRecognitionConstructor(scope: object = globalThis): SpeechRecognitionConstructor | null {
  const s = scope as SpeechScope;
  return s.SpeechRecognition ?? s.webkitSpeechRecognition ?? null;
}

export function detectVoiceCapabilities(scope: object = globalThis): VoiceCapabilities {
  const s = scope as SpeechScope;
  const synth = s.speechSynthesis;
  return {
    recognition: getRecognitionConstructor(scope) !== null,
    synthesis: Boolean(synth) && typeof s.SpeechSynthesisUtterance === "function",
    voices: Boolean(synth) && typeof synth?.getVoices === "function",
    microphoneList: typeof s.navigator?.mediaDevices?.enumerateDevices === "function",
  };
}

/** Picks the named voice, else a voice for the language, else a voice with the same base language. */
export function findVoice(voices: readonly SpeechSynthesisVoice[], name: string | null, lang: string): SpeechSynthesisVoice | null {
  if (name) {
    const exact = voices.find((voice) => voice.name === name);
    if (exact) return exact;
  }
  const wanted = lang.toLowerCase();
  const base = wanted.split("-")[0] ?? wanted;
  return (
    voices.find((voice) => voice.lang.toLowerCase() === wanted) ??
    voices.find((voice) => voice.lang.toLowerCase().split("-")[0] === base) ??
    null
  );
}

export function clampRate(value: number): number {
  return Math.min(2, Math.max(0.5, value));
}

/** How spoken output sounds: the settings the viewer chose on the Voice page. */
export interface SpeechOutputOptions {
  lang: string;
  voiceName: string | null;
  speakingRate: number;
  pitch: number;
}

/** The output options from the saved voice settings. Anything not saved yet takes the same default the Voice page uses. */
export function speechOutputOptions(voice: { language?: string; voice_name?: string | null; speaking_rate?: number; pitch?: number } | undefined): SpeechOutputOptions {
  return {
    lang: voice?.language ?? "en-US",
    voiceName: voice?.voice_name ?? null,
    speakingRate: voice?.speaking_rate ?? 1,
    pitch: voice?.pitch ?? 1,
  };
}

/** Applies the output options to an utterance. The rate and pitch are kept inside the range the browser accepts. */
export function configureUtterance(
  utterance: SpeechSynthesisUtterance,
  options: SpeechOutputOptions,
  voices: readonly SpeechSynthesisVoice[],
): SpeechSynthesisUtterance {
  utterance.lang = options.lang;
  utterance.rate = clampRate(options.speakingRate);
  utterance.pitch = Math.min(2, Math.max(0.5, options.pitch));
  const voice = findVoice(voices, options.voiceName, options.lang);
  if (voice) utterance.voice = voice;
  return utterance;
}

export function recognitionErrorMessage(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was blocked. Allow microphone access in the browser and try again.";
    case "audio-capture":
      return "No microphone was found. Connect one, or use the text box.";
    case "network":
      return "The browser speech service could not be reached. Use the text box instead.";
    case "language-not-supported":
      return "The browser does not support the selected speech language.";
    default:
      return `Speech recognition stopped (${code}).`;
  }
}

/** One engine session that listens for one utterance. Callers decide whether to restart it. */
export interface SessionHandlers {
  onStart: () => void;
  onSpeechStart: () => void;
  onInterim: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (code: string) => void;
  onEnd: () => void;
}

export function createRecognitionSession(lang: string, handlers: SessionHandlers): SpeechRecognitionLike | null {
  const Constructor = getRecognitionConstructor();
  if (!Constructor) return null;
  const recognition = new Constructor();
  recognition.lang = lang;
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.onstart = handlers.onStart;
  recognition.onspeechstart = handlers.onSpeechStart;
  recognition.onresult = (event: SpeechRecognitionEventLike) => {
    let finalText = "";
    let interimText = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const transcript = result?.[0]?.transcript ?? "";
      if (result?.isFinal) finalText += transcript;
      else interimText += transcript;
    }
    handlers.onInterim(interimText);
    if (finalText.trim()) handlers.onFinal(finalText.trim());
  };
  recognition.onerror = (event) => handlers.onError(event.error);
  recognition.onend = handlers.onEnd;
  return recognition;
}
