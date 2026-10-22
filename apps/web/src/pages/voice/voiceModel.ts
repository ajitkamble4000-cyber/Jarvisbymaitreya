import type { OrbState } from "../../components/orb/AIOrb";
import type { VoiceCapabilities } from "../../services/speech";
import type { VoiceState } from "../../stores/voice-store";

/** Maps the voice state machine onto the orb. Pure. */
export function orbStateForVoice(state: VoiceState): OrbState {
  switch (state) {
    case "listening":
      return "listening";
    case "processing":
      return "processing";
    case "speaking":
      return "speaking";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

export type CapabilityStatus = "available" | "unavailable" | "active" | "inactive" | "not_available";

export interface CapabilityRow {
  id: string;
  label: string;
  status: CapabilityStatus;
  detail: string;
}

export interface CapabilityInput {
  capabilities: VoiceCapabilities;
  voiceCount: number;
  continuousRequested: boolean;
  recognitionActive: boolean;
}

/**
 * What the browser can do, in plain words. Continuous listening is "active" only while the
 * recognition engine is really running. Wake word is always "not available". Pure.
 */
export function capabilityRows(input: CapabilityInput): CapabilityRow[] {
  const { capabilities, voiceCount, continuousRequested, recognitionActive } = input;
  return [
    {
      id: "speech-input",
      label: "Speech input",
      status: capabilities.recognition ? "available" : "unavailable",
      detail: capabilities.recognition ? "The browser can transcribe your voice." : "This browser cannot transcribe speech. Use the text box.",
    },
    {
      id: "speech-output",
      label: "Speech output",
      status: capabilities.synthesis ? "available" : "unavailable",
      detail: capabilities.synthesis ? "The browser can read replies aloud." : "This browser cannot read replies aloud. Replies stay as text.",
    },
    {
      id: "voices",
      label: "Voices",
      status: capabilities.voices && voiceCount > 0 ? "available" : "unavailable",
      detail: capabilities.voices && voiceCount > 0 ? `${voiceCount} voices installed on this device.` : "No voices are listed by this browser. The default voice is used.",
    },
    {
      id: "microphone-selection",
      label: "Microphone selection",
      status: capabilities.microphoneList ? "available" : "unavailable",
      detail: capabilities.microphoneList ? "Choose the microphone below." : "Microphone selection is not supported in this browser.",
    },
    {
      id: "continuous-listening",
      label: "Continuous listening",
      status: recognitionActive && continuousRequested ? "active" : "inactive",
      detail:
        recognitionActive && continuousRequested
          ? "Listening continuously. It restarts after each sentence."
          : continuousRequested
            ? "Requested, but not running yet. Press the microphone button to start."
            : "Off. Turn it on to listen without pressing the button.",
    },
    {
      id: "wake-word",
      label: "Wake word",
      status: "not_available",
      detail: "Not available. No wake-word engine is bundled with JARVIS.",
    },
  ];
}

export type PushMode = "hold" | "toggle";

/** settings.voice.push_to_talk: true is hold to talk, false is press once to start and again to stop. Pure. */
export function pushModeFor(pushToTalk: boolean | undefined): PushMode {
  return pushToTalk === false ? "toggle" : "hold";
}

export function micButtonLabel(mode: PushMode, active: boolean): string {
  if (mode === "toggle") return active ? "Stop listening" : "Start listening";
  return active ? "Release to send" : "Hold to talk";
}

/** Converts a settings speaking rate into the range the synthesis engine accepts. */
export function speakingRateLabel(rate: number): string {
  return `${rate.toFixed(1)}×`;
}
