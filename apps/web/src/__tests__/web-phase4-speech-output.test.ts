import { describe, expect, it } from "vitest";
import { configureUtterance, speechOutputOptions } from "../services/speech";

function blankUtterance(): SpeechSynthesisUtterance {
  return { lang: "", rate: 1, pitch: 1, voice: null } as unknown as SpeechSynthesisUtterance;
}

const VOICES = [
  { name: "Daniel", lang: "en-GB" },
  { name: "Amélie", lang: "fr-FR" },
  { name: "Thomas", lang: "fr-CA" },
] as unknown as SpeechSynthesisVoice[];

describe("speech output options", () => {
  it("uses the Voice page defaults when nothing is saved", () => {
    expect(speechOutputOptions(undefined)).toEqual({ lang: "en-US", voiceName: null, speakingRate: 1, pitch: 1 });
  });

  it("takes the saved values", () => {
    expect(speechOutputOptions({ language: "fr-FR", voice_name: "Amélie", speaking_rate: 0.8, pitch: 1.1 })).toEqual({
      lang: "fr-FR",
      voiceName: "Amélie",
      speakingRate: 0.8,
      pitch: 1.1,
    });
  });
});

describe("configureUtterance", () => {
  it("keeps the rate and pitch inside the range the browser accepts", () => {
    const fast = configureUtterance(blankUtterance(), { lang: "en-US", voiceName: null, speakingRate: 5, pitch: 0.1 }, []);
    expect(fast.rate).toBe(2);
    expect(fast.pitch).toBe(0.5);
  });

  it("uses the named voice when it is installed", () => {
    const utterance = configureUtterance(blankUtterance(), { lang: "fr-FR", voiceName: "Thomas", speakingRate: 1, pitch: 1 }, VOICES);
    expect(utterance.lang).toBe("fr-FR");
    expect(utterance.voice).toEqual(expect.objectContaining({ name: "Thomas" }));
  });

  it("falls back to a voice for the language when the named one is missing", () => {
    const utterance = configureUtterance(blankUtterance(), { lang: "fr-FR", voiceName: "Not installed", speakingRate: 1, pitch: 1 }, VOICES);
    expect(utterance.voice).toEqual(expect.objectContaining({ name: "Amélie" }));
  });

  it("leaves the browser's own voice alone when no voice matches", () => {
    const utterance = configureUtterance(blankUtterance(), { lang: "ja-JP", voiceName: null, speakingRate: 1, pitch: 1 }, VOICES);
    expect(utterance.voice).toBeNull();
  });
});
