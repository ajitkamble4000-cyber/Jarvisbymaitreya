import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { StatusDot } from "../../components/ui/StatusDot";
import { useToast } from "../../components/ui/Toast";
import { ApiError } from "../../services/api";
import { useConversationQuery } from "../../services/queries";
import type { MessageOut } from "../../types/api";
import { Composer } from "./Composer";
import { ConversationList } from "./ConversationList";
import { ExportButtons } from "./ExportButtons";
import { promptFromSearch } from "./deepLink";
import { MessageBubble } from "./MessageBubble";
import { ToolProposalCard } from "./ToolProposalCard";
import { lastAssistantMessageId, lastUserMessageId, mergeMessages, messagesMatching } from "./chatModel";
import { useChatStream } from "./useChatStream";

function syntheticMessage(partial: Pick<MessageOut, "id" | "role" | "content" | "status" | "conversation_id"> & Partial<MessageOut>): MessageOut {
  return {
    provider_id: null,
    model: null,
    task_id: null,
    meta: {},
    created_at: new Date().toISOString(),
    edited_at: null,
    ...partial,
  };
}

/** How far below the screen the end of the messages may be and still count as "at the end", in pixels. */
const FOLLOW_TOLERANCE_PX = 24;

/**
 * AI Chat: conversation list on the left, messages and composer on the right. Streaming tokens
 * appear in a live region. Stop, copy, regenerate and edit act on real endpoints.
 */
export default function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // A /chat?prompt=... link fills the composer once for each new link. It never sends the text.
  const [prefill, setPrefill] = useState({ id: 0, text: "", truncated: false });
  useEffect(() => {
    const link = promptFromSearch(location.search);
    if (link.text) setPrefill((current) => ({ id: current.id + 1, ...link }));
  }, [location.search]);
  const detail = useConversationQuery(conversationId);
  const chat = useChatStream({
    onConversationCreated: (id) => navigate(`/chat/${id}`, { replace: true }),
  });
  const { state } = chat;
  const { toast } = useToast();
  const endRef = useRef<HTMLDivElement>(null);
  // Whether the end of the messages is on screen. While it is, a streaming reply is followed. Once the reader scrolls
  // up to read earlier text, it is not, and "Jump to latest" brings them back.
  const followingRef = useRef(true);
  const [following, setFollowing] = useState(true);

  // Leaving for a different conversation stops the reply in progress. A new conversation keeps it.
  useEffect(() => {
    if (conversationId && state.conversationId && conversationId !== state.conversationId && chat.isStreaming) {
      chat.stop();
    }
    // chat.stop is stable; the route and stream conversation drive this effect.
  }, [conversationId, state.conversationId, chat.isStreaming]);

  const viewingStream = conversationId !== undefined && state.conversationId === conversationId;
  const persisted = detail.data?.messages ?? [];

  const extras = useMemo<MessageOut[]>(() => {
    if (!viewingStream || !conversationId) return [];
    const list: MessageOut[] = [];
    if (state.optimisticUser) list.push(state.optimisticUser);
    else if (state.pendingUser) {
      list.push(syntheticMessage({ id: "pending-user", role: "user", content: state.pendingUser, status: "complete", conversation_id: conversationId }));
    }
    if (state.status === "streaming" || state.text) {
      list.push(
        syntheticMessage({
          id: state.messageId ?? "pending-assistant",
          role: "assistant",
          content: state.text,
          status: "streaming",
          conversation_id: conversationId,
          provider_id: state.providerId,
          model: state.model,
          meta: state.fallback ? { fallback_used: state.fallback } : {},
        }),
      );
    }
    return list;
  }, [viewingStream, conversationId, state]);

  const [finding, setFinding] = useState("");
  const allMessages = mergeMessages(persisted, extras);
  const messages = messagesMatching(allMessages, finding);
  const lastUser = lastUserMessageId(persisted);
  const lastAssistant = lastAssistantMessageId(persisted);

  // A different conversation always opens at its end, even when the reader had scrolled up in the last one.
  useEffect(() => {
    followingRef.current = true;
    setFollowing(true);
  }, [conversationId]);

  const scrollKey = `${messages.length}:${state.text.length}`;
  useEffect(() => {
    if (followingRef.current) endRef.current?.scrollIntoView?.({ block: "end" });
  }, [scrollKey, conversationId]);

  // Only the reader's own scrolling changes whether the end is followed. Growing content does not, so a reply that
  // streams in cannot leave the reader behind.
  useEffect(() => {
    const onScroll = (): void => {
      const end = endRef.current;
      if (!end) return;
      const onScreen = end.getBoundingClientRect().top <= window.innerHeight + FOLLOW_TOLERANCE_PX;
      followingRef.current = onScreen;
      setFollowing(onScreen);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const jumpToLatest = (): void => {
    followingRef.current = true;
    setFollowing(true);
    endRef.current?.scrollIntoView?.({ block: "end" });
  };

  const notFound = detail.isError && detail.error instanceof ApiError && detail.error.status === 404;
  // A conversation that has just been created has no cached detail yet. Its streamed reply must
  // stay visible while that first fetch runs, so the skeleton only replaces the message log.
  const loadingHistory = conversationId !== undefined && detail.isPending && !viewingStream;
  const title = detail.data?.conversation.title ?? (conversationId ? "Conversation" : "New conversation");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="AI chat"
        description="Ask questions, plan work, or propose an action. Device actions wait for approval where your settings require it."
      />
      <div className="grid gap-6 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <Card className="p-3 sm:p-4">
          <ConversationList activeId={conversationId} />
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          {notFound ? (
            <EmptyState
              title="This conversation no longer exists"
              description="It may have been deleted. Start a new chat instead."
              action={
                <Link to="/chat" className="text-sm text-accent underline underline-offset-4">
                  New chat
                </Link>
              }
            />
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <h2 className="min-w-0 truncate text-lg font-semibold text-ink">{title}</h2>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-3">
                  {chat.isStreaming ? <StatusDot tone="info" label="Replying" pulse /> : null}
                  {conversationId && detail.data ? <ExportButtons conversationId={conversationId} /> : null}
                </div>
              </div>

              {allMessages.length > 0 ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Input
                    type="search"
                    aria-label="Find in this conversation"
                    placeholder="Find in this conversation"
                    value={finding}
                    onChange={(event) => setFinding(event.target.value)}
                    className="sm:max-w-xs"
                  />
                  <p role="status" aria-live="polite" className="text-sm text-muted">
                    {finding.trim() ? `${messages.length} of ${allMessages.length} messages match.` : ""}
                  </p>
                </div>
              ) : null}

              <div
                role="log"
                aria-label="Messages"
                aria-busy={chat.isStreaming || loadingHistory || undefined}
                className="flex min-h-64 flex-col gap-4 rounded-card border border-line bg-canvas/40 p-4"
              >
                {loadingHistory ? (
                  <SkeletonList rows={3} label="Loading conversation" />
                ) : detail.isError && !viewingStream ? (
                  <ErrorState message={detail.error.message} onRetry={() => void detail.refetch()} />
                ) : allMessages.length > 0 && messages.length === 0 ? (
                  <p className="m-auto max-w-sm text-center text-sm text-muted">No message in this conversation matches “{finding.trim()}”.</p>
                ) : messages.length === 0 ? (
                  <p className="m-auto max-w-sm text-center text-sm text-muted">
                    Nothing here yet. Type a message below to start. Replies stream in as they are written.
                  </p>
                ) : (
                  messages.map((message) => {
                    const streamingAssistant = message.status === "streaming";
                    const editable = message.role === "user" && message.id === lastUser && !chat.isStreaming && !message.id.startsWith("pending");
                    const regenerable = message.role === "assistant" && message.id === lastAssistant && !chat.isStreaming;
                    return (
                      <div key={message.id} aria-live={streamingAssistant ? "polite" : undefined}>
                        <MessageBubble
                          message={message}
                          canEdit={editable}
                          canRegenerate={regenerable}
                          disabled={chat.isStreaming || chat.isCreating}
                          onEdit={(messageId, content) => {
                            if (conversationId) void chat.editAndRegenerate(conversationId, messageId, content);
                          }}
                          onRegenerate={(messageId) => {
                            if (conversationId) void chat.regenerate(conversationId, messageId);
                          }}
                        />
                      </div>
                    );
                  })
                )}
                {viewingStream
                  ? state.proposals.map((proposal) => (
                      <ToolProposalCard key={proposal.task.id} task={proposal.task} decision={proposal.decision} reason={proposal.reason} />
                    ))
                  : null}
                <div ref={endRef} />
              </div>

              {!following && messages.length > 0 ? (
                <div className="sticky bottom-4 z-10 flex justify-end">
                  <Button size="sm" variant="secondary" onClick={jumpToLatest}>
                    Jump to latest
                  </Button>
                </div>
              ) : null}

              {viewingStream && state.error ? (
                <div role="alert" className="rounded-lg border border-danger/50 bg-danger/10 p-3 text-sm">
                  <p className="font-medium text-danger">Reply failed</p>
                  <p className="text-ink">{state.error.message}</p>
                </div>
              ) : null}
              {viewingStream && state.status === "stopped" ? (
                <p role="status" className="text-sm text-muted">
                  Stopped. The partial reply is kept and marked as stopped.
                </p>
              ) : null}
              {viewingStream && state.status === "failed" && !state.error ? (
                <p role="alert" className="text-sm font-medium text-danger">
                  The reply could not be completed.
                </p>
              ) : null}
              {chat.editError ? (
                <p role="alert" className="text-sm font-medium text-danger">
                  {chat.editError}
                </p>
              ) : null}

              {prefill.text ? (
                <p role="status" className="text-xs text-muted">
                  {prefill.truncated ? "The link's prompt was cut to 2000 characters. " : ""}
                  Text from a link is in the box below. Nothing has been sent.
                </p>
              ) : null}
              <Composer
                // Keyed by conversation, so each one starts from its own unsent draft instead of the last one typed.
                key={`${conversationId ?? "new"}:${prefill.id}`}
                draftScope={conversationId ?? "new"}
                initialValue={prefill.text}
                disabled={chat.isCreating}
                streaming={chat.isStreaming}
                onStop={chat.stop}
                onSend={(text) => {
                  // The stream reports its own errors in the conversation. This catches a failed conversation creation.
                  chat.send(text, conversationId).catch((error: unknown) => {
                    toast({
                      tone: "error",
                      title: "Message not sent",
                      description: error instanceof Error ? error.message : "The conversation could not be created.",
                    });
                  });
                }}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
