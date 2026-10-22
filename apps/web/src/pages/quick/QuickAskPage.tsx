import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../components/ui/Icon";
import { Input } from "../../components/ui/Input";
import { ToolProposalCard } from "../chat/ToolProposalCard";
import { useWakeWord } from "../../hooks/useWakeWord";
import { useVoice } from "../../hooks/useVoice";
import { ApiError } from "../../services/api";
import { streamConversationMessage } from "../../services/chatStream";
import { EMPTY_QUICK_REPLY, quickTitle, reduceQuickReply, type QuickReply } from "../../services/quickAsk";
import { useCreateConversation, useSettingsQuery } from "../../services/queries";
import { speechOutputOptions } from "../../services/speech";
import { voiceStore, useVoiceSnapshot } from "../../stores/voice-store";

const WAKE_STATUS_TEXT: Record<string, string> = {
  listening: "Say “Hey JARVIS” and your request.",
  heard: "Listening for your request…",
};

/**
 * The quick window: one request typed or spoken, and the reply with its approval cards. It opens from the desktop
 * popup (Ctrl+Space) or from a "Hey JARVIS" request on another page, which arrives as ?ask=. Escape closes the popup
 * when the desktop bridge is present. In a browser tab it clears the box.
 */
export default function QuickAskPage() {
  const [params, setParams] = useSearchParams();
  const settings = useSettingsQuery();
  const createConversation = useCreateConversation();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<QuickReply>(EMPTY_QUICK_REPLY);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const autoAskDone = useRef(false);

  const voiceSettings = settings.data?.voice;
  const voice = useVoice({
    ...speechOutputOptions(voiceSettings),
    continuous: false,
    interruptions: false,
    onFinalTranscript: (spoken) => void ask(spoken),
  });
  const snapshot = useVoiceSnapshot(voiceStore);
  const wake = useWakeWord({
    enabled: voiceSettings?.wake_word_enabled ?? false,
    // The microphone button and the wake word never listen at the same time.
    paused: snapshot.recognitionActive,
    lang: voiceSettings?.language ?? "en-US",
    onCommand: (command) => void ask(command),
  });

  useEffect(() => {
    inputRef.current?.focus();
    return () => abortRef.current?.abort();
  }, []);

  const ask = async (request: string): Promise<void> => {
    const content = request.trim();
    if (content === "" || busy) return;
    setBusy(true);
    setReply(EMPTY_QUICK_REPLY);
    setText("");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      // Each quick ask opens its own conversation, so its history stays findable in Chat.
      const created = await createConversation.mutateAsync(quickTitle(content));
      setConversationId(created.id);
      let answer = EMPTY_QUICK_REPLY;
      for await (const event of streamConversationMessage(created.id, { content }, controller.signal)) {
        answer = reduceQuickReply(answer, event);
        setReply(answer);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      const message = error instanceof ApiError ? error.message : "The request could not be completed.";
      setReply((current) => ({ ...current, error: message, done: true }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  };

  // A request handed over in the address, such as the one from a "Hey JARVIS" on another page, is sent once.
  const askParam = params.get("ask");
  useEffect(() => {
    if (!askParam || autoAskDone.current) return;
    autoAskDone.current = true;
    setParams({}, { replace: true });
    void ask(askParam);
    // ask is recreated on each render; the handover is read once, on the first render that has it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askParam]);

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    void ask(text);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    if (window.jarvisQuick) {
      window.jarvisQuick.hide();
    } else {
      setText("");
    }
  };

  const wakeText = useMemo(() => {
    if (wake.message) return wake.message;
    return WAKE_STATUS_TEXT[wake.status] ?? null;
  }, [wake.message, wake.status]);

  return (
    <main id="main-content" tabIndex={-1} className="flex min-h-screen flex-col gap-4 bg-canvas p-4 text-ink outline-none" onKeyDown={onKeyDown}>
      <header className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">JARVIS</p>
        {conversationId ? (
          <Link to={`/chat/${conversationId}`} className="text-xs text-accent underline underline-offset-4">
            Open in chat
          </Link>
        ) : null}
      </header>

      <form onSubmit={onSubmit} className="flex items-center gap-2">
        <label htmlFor="quick-request" className="sr-only">
          What should JARVIS do?
        </label>
        <Input
          id="quick-request"
          ref={inputRef}
          autoComplete="off"
          placeholder="What should JARVIS do?"
          value={text}
          disabled={busy}
          onChange={(event) => setText(event.target.value)}
          className="min-w-0 flex-1"
        />
        {voice.capabilities.recognition ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            aria-pressed={snapshot.recognitionActive}
            aria-label={snapshot.recognitionActive ? "Stop listening" : "Speak your request"}
            onClick={() => (snapshot.recognitionActive ? voice.stopListening() : voice.startListening())}
          >
            <Icon name="voice" className="size-4" />
          </Button>
        ) : null}
        <Button type="submit" size="sm" disabled={busy || text.trim() === ""} aria-label="Send">
          <Icon name="send" className="size-4" />
        </Button>
      </form>

      {snapshot.recognitionActive && voice.interim ? <p className="text-sm text-muted">{voice.interim}</p> : null}
      {wakeText ? (
        <p role="status" className="text-xs text-muted">
          {wakeText}
        </p>
      ) : null}

      <section aria-live="polite" aria-label="Reply" className="flex flex-col gap-3">
        {reply.error ? (
          <p role="alert" className="rounded-lg border border-danger/50 bg-danger/10 p-3 text-sm text-ink">
            {reply.error}
          </p>
        ) : null}
        {reply.text ? <p className="whitespace-pre-wrap text-sm">{reply.text}</p> : null}
        {reply.proposals.map((proposal) => (
          <ToolProposalCard key={proposal.task.id} task={proposal.task} decision={proposal.decision} reason={proposal.reason} />
        ))}
        {busy && !reply.text ? <p className="text-sm text-muted">Working on it…</p> : null}
      </section>

      <footer className="mt-auto text-xs text-muted">
        <kbd>Enter</kbd> to send. <kbd>Esc</kbd> to close the window.
      </footer>
    </main>
  );
}
