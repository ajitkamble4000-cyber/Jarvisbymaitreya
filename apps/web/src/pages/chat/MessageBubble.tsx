import { useState } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../components/ui/Icon";
import { IconButton } from "../../components/ui/IconButton";
import { Markdown } from "../../components/markdown/Markdown";
import { Textarea } from "../../components/ui/Input";
import { useToast } from "../../components/ui/Toast";
import { useMessageFeedback } from "../../services/queries";
import { formatDateTime, humanize } from "../../services/format";
import type { MessageOut } from "../../types/api";
import { feedbackRating, nextFeedback, type FeedbackRating } from "./chatModel";
import { ReadAloudButton } from "./ReadAloudButton";
import { TaskProposalById } from "./ToolProposalCard";

export interface MessageBubbleProps {
  message: MessageOut;
  canEdit: boolean;
  canRegenerate: boolean;
  disabled: boolean;
  onEdit?: (messageId: string, content: string) => void;
  onRegenerate?: (messageId: string) => void;
}

/** One chat message. User text is plain. Assistant text is rendered as safe Markdown. */
export function MessageBubble({ message, canEdit, canRegenerate, disabled, onEdit, onRegenerate }: MessageBubbleProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const feedback = useMessageFeedback();
  const { toast } = useToast();
  const rating = feedbackRating(message.meta);

  // Pressing the rating already given clears it. The server keeps one rating per reply.
  const rate = (pressed: FeedbackRating): void => {
    feedback.mutate(
      { conversationId: message.conversation_id, messageId: message.id, rating: nextFeedback(rating, pressed) },
      { onError: (error) => toast({ tone: "error", title: "Rating not saved", description: error.message }) },
    );
  };
  const isUser = message.role === "user";
  const fallback = message.meta?.fallback_used ?? null;

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.setTimeout(() => setCopyState("idle"), 2000);
  };

  const saveEdit = (): void => {
    const next = draft.trim();
    if (!next) return;
    setEditing(false);
    onEdit?.(message.id, next);
  };

  return (
    <article
      aria-label={`${isUser ? "You" : "JARVIS"}, ${formatDateTime(message.created_at)}`}
      className={isUser ? "flex justify-end" : "flex justify-start"}
    >
      <div className={isUser ? "flex max-w-[85%] flex-col items-end gap-1" : "flex w-full max-w-[92%] flex-col items-start gap-1"}>
        <span className="text-xs text-muted">{isUser ? "You" : message.provider_id ? `JARVIS · ${humanize(message.provider_id)}` : "JARVIS"}</span>
        <div
          className={
            isUser
              ? "rounded-2xl rounded-br-md border border-accent/40 bg-accent/15 px-4 py-2.5 text-sm text-ink"
              : "w-full rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3 text-ink"
          }
        >
          {editing ? (
            <div className="flex flex-col gap-2">
              <Textarea aria-label="Edit message" value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(message.content); }}>
                  Cancel
                </Button>
                <Button size="sm" variant="primary" disabled={!draft.trim() || disabled} onClick={saveEdit}>
                  Save and regenerate
                </Button>
              </div>
            </div>
          ) : isUser ? (
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          ) : message.content ? (
            <Markdown source={message.content} />
          ) : (
            <p className="text-sm text-muted">{message.status === "streaming" ? "Thinking…" : "No text was saved for this reply."}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {message.status === "cancelled" ? <Badge tone="warning">Stopped</Badge> : null}
          {message.status === "error" ? <Badge tone="danger">Failed</Badge> : null}
          {fallback ? (
            <Badge tone="info">
              Answered by {humanize(fallback.provider_id)} because {fallback.reason}
            </Badge>
          ) : null}
          {message.task_id ? <TaskProposalWrapper taskId={message.task_id} /> : null}
        </div>

        {!editing ? (
          <div className="flex items-center gap-1">
            {!isUser && message.content ? (
              <IconButton label={copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy reply"} icon={<Icon name="copy" className="size-4" />} onClick={() => void copy()} />
            ) : null}
            {!isUser && message.status === "complete" && message.content ? <ReadAloudButton text={message.content} /> : null}
            {!isUser && message.status === "complete" ? (
              <>
                <IconButton
                  label="Helpful reply"
                  aria-pressed={rating === "up"}
                  icon={<Icon name="thumbUp" className="size-4" />}
                  disabled={disabled || feedback.isPending}
                  onClick={() => rate("up")}
                />
                <IconButton
                  label="Not helpful"
                  aria-pressed={rating === "down"}
                  icon={<Icon name="thumbDown" className="size-4" />}
                  disabled={disabled || feedback.isPending}
                  onClick={() => rate("down")}
                />
              </>
            ) : null}
            {isUser && canEdit ? (
              <IconButton label="Edit message" icon={<Icon name="edit" className="size-4" />} disabled={disabled} onClick={() => { setDraft(message.content); setEditing(true); }} />
            ) : null}
            {!isUser && canRegenerate ? (
              <IconButton label="Regenerate reply" icon={<Icon name="refresh" className="size-4" />} disabled={disabled} onClick={() => onRegenerate?.(message.id)} />
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}

function TaskProposalWrapper({ taskId }: { taskId: string }) {
  return (
    <div className="w-full min-w-64 max-w-md">
      <TaskProposalById taskId={taskId} />
    </div>
  );
}
