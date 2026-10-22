import { describe, expect, it, vi } from "vitest";
import { INITIAL_VOICE_SNAPSHOT, createVoiceStore, voiceReducer, type VoiceSnapshot } from "../stores/voice-store";

const start = (snapshot: VoiceSnapshot = INITIAL_VOICE_SNAPSHOT) => voiceReducer(snapshot, { type: "RECOGNITION_STARTED" });

describe("voice state machine", () => {
  it("shows listening only after recognition has started", () => {
    expect(INITIAL_VOICE_SNAPSHOT.state).toBe("idle");
    const listening = start();
    expect(listening.state).toBe("listening");
    expect(listening.recognitionActive).toBe(true);
  });

  it("returns to idle when recognition ends without a transcript", () => {
    const ended = voiceReducer(start(), { type: "RECOGNITION_ENDED" });
    expect(ended.state).toBe("idle");
    expect(ended.recognitionActive).toBe(false);
  });

  it("moves from listening to processing on a final transcript and ignores blank text", () => {
    const processing = voiceReducer(start(), { type: "TRANSCRIPT_FINAL", text: "  open the chrome  " });
    expect(processing.state).toBe("processing");
    expect(processing.lastTranscript).toBe("open the chrome");
    expect(voiceReducer(start(), { type: "TRANSCRIPT_FINAL", text: "   " })).toEqual(start());
  });

  it("goes processing, speaking, idle for a normal reply", () => {
    const processing = voiceReducer(INITIAL_VOICE_SNAPSHOT, { type: "TRANSCRIPT_FINAL", text: "hello" });
    const speaking = voiceReducer(processing, { type: "SPEAK_STARTED", text: "Hi there." });
    expect(speaking.state).toBe("speaking");
    expect(speaking.lastSpoken).toBe("Hi there.");
    const idle = voiceReducer(speaking, { type: "SPEAK_ENDED" });
    expect(idle.state).toBe("idle");
  });

  it("returns to listening when the viewer talks over a reply (barge-in)", () => {
    const speaking = voiceReducer(start(), { type: "SPEAK_STARTED", text: "reply" });
    const interrupted = voiceReducer(speaking, { type: "INTERRUPTED" });
    expect(interrupted.state).toBe("listening");
  });

  it("returns to listening, not idle, when speech ends while the microphone is still on", () => {
    const speaking = voiceReducer(start(), { type: "SPEAK_STARTED", text: "reply" });
    expect(voiceReducer(speaking, { type: "SPEAK_ENDED" }).state).toBe("listening");
  });

  it("keeps speaking visible while continuous recognition restarts", () => {
    const speaking = voiceReducer(INITIAL_VOICE_SNAPSHOT, { type: "SPEAK_STARTED", text: "reply" });
    const restarted = voiceReducer(speaking, { type: "RECOGNITION_STARTED" });
    expect(restarted.state).toBe("speaking");
    expect(restarted.recognitionActive).toBe(true);
  });

  it("moves any state to error on failure and clears it on reset", () => {
    const failed = voiceReducer(start(), { type: "FAILED", message: "Microphone access was blocked." });
    expect(failed.state).toBe("error");
    expect(failed.error).toBe("Microphone access was blocked.");
    const reset = voiceReducer(failed, { type: "RESET" });
    expect(reset.state).toBe("idle");
    expect(reset.error).toBeNull();
  });

  it("returns the same snapshot for events that do not apply to the current state", () => {
    expect(voiceReducer(INITIAL_VOICE_SNAPSHOT, { type: "SPEAK_ENDED" })).toBe(INITIAL_VOICE_SNAPSHOT);
    expect(voiceReducer(INITIAL_VOICE_SNAPSHOT, { type: "RESPONSE_DONE" })).toBe(INITIAL_VOICE_SNAPSHOT);
    expect(voiceReducer(INITIAL_VOICE_SNAPSHOT, { type: "INTERRUPTED" })).toBe(INITIAL_VOICE_SNAPSHOT);
  });

  it("notifies subscribers once per real change", () => {
    const store = createVoiceStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.dispatch({ type: "SPEAK_ENDED" });
    expect(listener).not.toHaveBeenCalled();
    store.dispatch({ type: "RECOGNITION_STARTED" });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().state).toBe("listening");
    unsubscribe();
    store.dispatch({ type: "RECOGNITION_ENDED" });
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
