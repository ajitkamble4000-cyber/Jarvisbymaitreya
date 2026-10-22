import { describe, expect, it } from "vitest";
import { IDLE_WAKE_STATE, WAKE_FOLLOW_UP_MS, parseWakeWord, wakeStep } from "../services/wakeWord";

describe("parseWakeWord", () => {
  it("returns the request that follows the phrase, keeping its capitals", () => {
    expect(parseWakeWord("Hey JARVIS, open Google Chrome on my laptop")).toEqual({ woke: true, command: "open Google Chrome on my laptop" });
  });

  it.each(["hey jarvis", "Hey, Jarvis.", "hi jarvis", "OK Jarvis", "okay, jarvis!"])("accepts the greeting in %s", (phrase) => {
    expect(parseWakeWord(phrase)).toEqual({ woke: true, command: "" });
  });

  it("finds the phrase in the middle of an utterance", () => {
    expect(parseWakeWord("so yeah hey jarvis remind me at five")).toEqual({ woke: true, command: "remind me at five" });
  });

  it("does not wake on the name alone or on other words that contain it", () => {
    expect(parseWakeWord("tell jarvis later")).toEqual({ woke: false, command: "" });
    expect(parseWakeWord("jarvisbymaitreya is a repo")).toEqual({ woke: false, command: "" });
    expect(parseWakeWord("they said hey jarvisbymaitreya")).toEqual({ woke: false, command: "" });
  });

  it("does not wake on ordinary speech", () => {
    expect(parseWakeWord("open the calendar")).toEqual({ woke: false, command: "" });
  });
});

describe("wakeStep", () => {
  const now = 1_000_000;

  it("acts at once when the phrase comes with a request", () => {
    const step = wakeStep(IDLE_WAKE_STATE, "hey jarvis open chrome", now);
    expect(step).toEqual({ state: IDLE_WAKE_STATE, command: "open chrome", woke: true });
  });

  it("waits for the request when the phrase stands alone", () => {
    const step = wakeStep(IDLE_WAKE_STATE, "hey jarvis", now);
    expect(step.command).toBeNull();
    expect(step.woke).toBe(true);
    expect(step.state.awaitingSince).toBe(now);
  });

  it("takes the next utterance as the request while it is waiting", () => {
    const waiting = wakeStep(IDLE_WAKE_STATE, "hey jarvis", now).state;
    const step = wakeStep(waiting, "open my downloads folder", now + 2000);
    expect(step.command).toBe("open my downloads folder");
    expect(step.state).toEqual(IDLE_WAKE_STATE);
  });

  it("forgets the wake phrase after the follow-up window", () => {
    const waiting = wakeStep(IDLE_WAKE_STATE, "hey jarvis", now).state;
    const step = wakeStep(waiting, "open my downloads folder", now + WAKE_FOLLOW_UP_MS + 1);
    expect(step.command).toBeNull();
  });

  it("ignores silence and blank transcripts", () => {
    expect(wakeStep(IDLE_WAKE_STATE, "   ", now)).toEqual({ state: IDLE_WAKE_STATE, command: null, woke: false });
  });

  it("does not turn ordinary speech into a request when no phrase was heard", () => {
    expect(wakeStep(IDLE_WAKE_STATE, "what a nice day", now).command).toBeNull();
  });
});
