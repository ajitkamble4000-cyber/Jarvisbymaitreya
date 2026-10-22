import { useLocation, useNavigate } from "react-router-dom";
import { useWakeWord } from "../hooks/useWakeWord";
import { useSettingsQuery } from "../services/queries";

/** Pages that already listen with their own microphone control, so the background listener steps aside. */
const OWN_MICROPHONE_PATHS = ["/voice", "/quick"];

/**
 * "Hey JARVIS" on every page of the app. A request spoken after the phrase opens the quick window with that request
 * sent at once. While the listener is on, a small indicator shows it, so the microphone is never on unseen.
 */
export function WakeWordListener() {
  const settings = useSettingsQuery();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const voice = settings.data?.voice;
  const wake = useWakeWord({
    enabled: voice?.wake_word_enabled ?? false,
    paused: OWN_MICROPHONE_PATHS.includes(pathname),
    lang: voice?.language ?? "en-US",
    onCommand: (command) => navigate(`/quick?ask=${encodeURIComponent(command)}`),
  });

  if (wake.status === "off" || wake.status === "unsupported") {
    return wake.message ? (
      <p role="status" className="fixed bottom-4 left-4 z-40 max-w-xs rounded-lg border border-line bg-surface p-2 text-xs text-muted shadow">
        {wake.message}
      </p>
    ) : null;
  }

  const label =
    wake.status === "heard"
      ? "JARVIS is listening for your request"
      : wake.status === "listening"
        ? "Listening for “Hey JARVIS”"
        : wake.message ?? "“Hey JARVIS” is off";
  return (
    <p
      role={wake.status === "blocked" || wake.status === "error" ? "alert" : "status"}
      className="fixed bottom-4 left-4 z-40 max-w-xs rounded-lg border border-line bg-surface p-2 text-xs text-ink shadow"
    >
      <span aria-hidden="true" className={wake.status === "heard" ? "mr-1 text-accent" : "mr-1 text-muted"}>
        ●
      </span>
      {label}
    </p>
  );
}
