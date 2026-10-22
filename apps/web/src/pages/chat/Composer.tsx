import { useState, type KeyboardEvent } from "react";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../components/ui/Icon";
import { Textarea } from "../../components/ui/Input";
import { Kbd } from "../../components/ui/Kbd";
import { readDraft, writeDraft } from "../../services/chatDrafts";

export interface ComposerProps {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** Text inserted from the voice page or a quick prompt. */
  initialValue?: string;
  /** Where the unsent text is kept in this browser, such as a conversation id. Omit to keep nothing. */
  draftScope?: string;
}

/**
 * Message box. Enter sends, Shift+Enter adds a line, and Stop replaces Send while a reply streams.
 * With a draft scope, unsent text survives leaving the page; inserted text takes its place until edited.
 */
export function Composer({ disabled, streaming, onSend, onStop, initialValue = "", draftScope }: ComposerProps) {
  const [text, setText] = useState(() => {
    if (initialValue) return initialValue;
    return draftScope === undefined ? "" : readDraft(draftScope);
  });

  const change = (value: string): void => {
    setText(value);
    if (draftScope !== undefined) writeDraft(draftScope, value);
  };

  const submit = (): void => {
    const trimmed = text.trim();
    if (!trimmed || disabled || streaming) return;
    change("");
    onSend(trimmed);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form
      className="flex flex-col gap-2 rounded-card border border-line bg-surface p-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor="chat-composer" className="sr-only">
        Message JARVIS
      </label>
      <Textarea
        id="chat-composer"
        value={text}
        onChange={(event) => change(event.target.value)}
        onKeyDown={onKeyDown}
        rows={2}
        placeholder="Ask JARVIS something, or describe an action on a device"
        className="min-h-14 border-0 bg-transparent px-1 focus-visible:outline-none"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">
          <Kbd>Enter</Kbd> to send, <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> for a new line
        </p>
        {streaming ? (
          <Button variant="secondary" size="sm" onClick={onStop} aria-label="Stop generating">
            <Icon name="stop" className="size-4" />
            Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" size="sm" disabled={disabled || !text.trim()}>
            <Icon name="send" className="size-4" />
            Send
          </Button>
        )}
      </div>
    </form>
  );
}
