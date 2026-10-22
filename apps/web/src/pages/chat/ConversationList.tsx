import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Icon } from "../../components/ui/Icon";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Tabs } from "../../components/ui/Tabs";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { formatRelative } from "../../services/format";
import { useConversationsQuery, useDeleteConversation, useUpdateConversation } from "../../services/queries";
import type { ConversationOut } from "../../types/api";
import { cn } from "../../components/ui/cn";

function RenameDialog({ conversation, onClose }: { conversation: ConversationOut; onClose: () => void }) {
  const update = useUpdateConversation();
  const [title, setTitle] = useState(conversation.title);
  const [error, setError] = useState<string | null>(null);

  const save = async (): Promise<void> => {
    const next = title.trim();
    if (!next) {
      setError("Enter a title.");
      return;
    }
    try {
      await update.mutateAsync({ id: conversation.id, patch: { title: next } });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The title could not be saved.");
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Rename conversation"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={update.isPending} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Title" error={error}>
        {(control) => <Input {...control} value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />}
      </Field>
    </Dialog>
  );
}

export interface ConversationListProps {
  activeId?: string;
}

/** Conversations with search, archive, rename and delete. Delete asks for confirmation first. */
export function ConversationList({ activeId }: ConversationListProps) {
  const navigate = useNavigate();
  const [view, setView] = useState<"active" | "archived">("active");
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search.trim(), 300);
  const list = useConversationsQuery({ query: debounced, archived: view === "archived" });
  const update = useUpdateConversation();
  const remove = useDeleteConversation();
  const [renaming, setRenaming] = useState<ConversationOut | null>(null);
  const [deleting, setDeleting] = useState<ConversationOut | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  return (
    <section aria-label="Conversations" className="flex min-w-0 flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button variant="primary" size="sm" className="flex-1" onClick={() => navigate("/chat")}>
          <Icon name="plus" className="size-4" />
          New chat
        </Button>
      </div>
      <Input type="search" aria-label="Search conversations" placeholder="Search conversations" value={search} onChange={(event) => setSearch(event.target.value)} />
      <Tabs
        label="Conversation view"
        value={view}
        onChange={(id) => setView(id as "active" | "archived")}
        items={[
          { id: "active", label: "Active" },
          { id: "archived", label: "Archived" },
        ]}
      />

      {actionError ? (
        <p role="alert" className="text-xs font-medium text-danger">
          {actionError}
        </p>
      ) : null}

      {list.isPending ? (
        <SkeletonList rows={3} label="Loading conversations" />
      ) : list.isError ? (
        <ErrorState message={list.error.message} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={debounced ? "No matches" : view === "archived" ? "Nothing archived" : "No conversations yet"} description={debounced ? "Try a different search." : "Send a message to start one."} />
      ) : (
        <ul className="flex max-h-[50vh] flex-col gap-1 overflow-y-auto pr-1 lg:max-h-[calc(100vh-14rem)]">
          {list.data.items.map((conversation) => {
            const selected = conversation.id === activeId;
            return (
              <li
                key={conversation.id}
                className={cn("group flex items-center gap-1 rounded-lg border px-2 py-1.5", selected ? "border-accent bg-raised" : "border-transparent hover:bg-raised")}
              >
                <Link to={`/chat/${conversation.id}`} aria-current={selected ? "page" : undefined} className="min-w-0 flex-1 py-1">
                  <span className="block truncate text-sm font-medium text-ink">{conversation.title || "Untitled"}</span>
                  <span className="block text-xs text-muted">
                    {conversation.pinned ? "Pinned · " : ""}
                    {formatRelative(conversation.last_message_at ?? conversation.updated_at)}
                  </span>
                </Link>
                <IconButton
                  label={conversation.pinned ? "Unpin conversation" : "Pin conversation"}
                  aria-pressed={conversation.pinned}
                  icon={<Icon name="pin" className="size-4" />}
                  disabled={update.isPending}
                  onClick={() =>
                    update.mutate(
                      { id: conversation.id, patch: { pinned: !conversation.pinned } },
                      { onError: (cause) => setActionError(cause.message) },
                    )
                  }
                />
                <IconButton label={`Rename ${conversation.title || "conversation"}`} icon={<Icon name="edit" className="size-4" />} onClick={() => setRenaming(conversation)} />
                <IconButton
                  label={conversation.archived ? "Restore conversation" : "Archive conversation"}
                  icon={<Icon name="archive" className="size-4" />}
                  disabled={update.isPending}
                  onClick={() =>
                    update.mutate(
                      { id: conversation.id, patch: { archived: !conversation.archived } },
                      { onError: (cause) => setActionError(cause.message) },
                    )
                  }
                />
                <IconButton label={`Delete ${conversation.title || "conversation"}`} icon={<Icon name="trash" className="size-4" />} onClick={() => setDeleting(conversation)} />
              </li>
            );
          })}
        </ul>
      )}

      {renaming ? <RenameDialog conversation={renaming} onClose={() => setRenaming(null)} /> : null}

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title="Delete this conversation?"
        description="Its messages are removed. This cannot be undone."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Keep
            </Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={() => {
                if (!deleting) return;
                const wasActive = deleting.id === activeId;
                remove.mutate(deleting.id, {
                  onSuccess: () => {
                    setDeleting(null);
                    if (wasActive) navigate("/chat", { replace: true });
                  },
                  onError: (cause) => setActionError(cause.message),
                });
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <span className="sr-only">Deleting {deleting?.title ?? "this conversation"}.</span>
      </Dialog>
    </section>
  );
}
