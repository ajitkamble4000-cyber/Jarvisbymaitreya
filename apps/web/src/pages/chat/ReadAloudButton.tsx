import { useEffect, useMemo, useRef, useState } from "react";
import { IconButton } from "../../components/ui/IconButton";
import { Icon } from "../../components/ui/Icon";
import { useSettingsQuery } from "../../services/queries";
import { configureUtterance, detectVoiceCapabilities, speechOutputOptions } from "../../services/speech";

/**
 * Reads one reply aloud with the browser's speech output. Shown only when the browser reports that it can speak.
 * The saved voice settings choose the voice, language, speed and pitch, as they do for spoken replies.
 * Its state follows the engine's own events, so "Stop" appears only while speech is really playing.
 */
export function ReadAloudButton({ text }: { text: string }) {
  const canSpeak = useMemo(() => detectVoiceCapabilities().synthesis, []);
  // Only a browser that can speak needs the settings, so the request is skipped everywhere else.
  const settings = useSettingsQuery(canSpeak);
  const [speaking, setSpeaking] = useState(false);
  // Only the button that started the speech may cancel it. The find box unmounts other bubbles while
  // a reply is playing, and those must not silence it.
  const startedHere = useRef(false);

  useEffect(() => {
    return () => {
      if (startedHere.current && typeof window !== "undefined") window.speechSynthesis?.cancel();
    };
  }, []);

  if (!canSpeak) return null;

  const finish = (): void => {
    startedHere.current = false;
    setSpeaking(false);
  };

  const start = (): void => {
    const synth = window.speechSynthesis;
    const utterance = configureUtterance(new SpeechSynthesisUtterance(text), speechOutputOptions(settings.data?.voice), synth.getVoices());
    utterance.onend = finish;
    utterance.onerror = finish;
    synth.cancel();
    synth.speak(utterance);
    startedHere.current = true;
    setSpeaking(true);
  };

  const stop = (): void => {
    window.speechSynthesis.cancel();
    finish();
  };

  return speaking ? (
    <IconButton label="Stop reading" aria-pressed="true" icon={<Icon name="stop" className="size-4" />} onClick={stop} />
  ) : (
    <IconButton label="Read reply aloud" icon={<Icon name="play" className="size-4" />} onClick={start} />
  );
}
