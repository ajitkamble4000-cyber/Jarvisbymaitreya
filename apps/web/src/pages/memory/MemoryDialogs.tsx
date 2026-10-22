import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { ApiError } from "../../services/api";
import { formatDateTime, humanize } from "../../services/format";
import { MEMORY_CLEAR_PHRASE, isClearPhraseMatch } from "./memoryModel";
import { useClearMemories, useMemoryHistory, useDeleteMemory, type MemoryItem } from "./memoryQueries";

function failureText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export interface DeleteMemoryDialogProps {
  item: MemoryItem | null;
  onClose: () => void;
  onDeleted: () => void;
}

/** Confirms one deletion. Deleting a single memory needs one click on the confirm button. */
export function DeleteMemoryDialog({ item, onClose, onDeleted }: DeleteMemoryDialogProps) {
  const remove = useDeleteMemory();
  const [error, setError] = useState<string | null>(null);

  const confirm = (): void => {
    if (!item) return;
    setError(null);
    remove.mutate(item.id, {
      onSuccess: () => onDeleted(),
      onError: (cause) => setError(failureText(cause, "The memory could not be deleted.")),
    });
  };

  return (
    <Dialog
      open={item !== null}
      onClose={onClose}
      title="Delete this memory?"
      description="JARVIS will stop using it. The change is recorded in its history."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Keep it
          </Button>
          <Button variant="danger" loading={remove.isPending} onClick={confirm}>
            Delete memory
          </Button>
        </>
      }
    >
      {error ? <ErrorState message={error} /> : null}
    </Dialog>
  );
}

export interface ClearMemoriesDialogProps {
  open: boolean;
  onClose: () => void;
  onCleared: (deleted: number) => void;
}

/**
 * Deletes every memory. The button stays disabled until the typed text matches the phrase exactly,
 * and the same phrase is sent to the API.
 */
export function ClearMemoriesDialog({ open, onClose, onCleared }: ClearMemoriesDialogProps) {
  const clear = useClearMemories();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const matches = isClearPhraseMatch(typed);

  const close = (): void => {
    setTyped("");
    setError(null);
    onClose();
  };

  const confirm = (): void => {
    if (!matches) return;
    setError(null);
    clear.mutate(undefined, {
      onSuccess: (result) => {
        setTyped("");
        onCleared(result.deleted);
      },
      onError: (cause) => setError(failureText(cause, "No memories were deleted.")),
    });
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Delete all memories?"
      description="Every memory is removed for good. This cannot be undone."
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button variant="danger" disabled={!matches} loading={clear.isPending} onClick={confirm}>
            Delete all memories
          </Button>
        </>
      }
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          confirm();
        }}
        className="flex flex-col gap-4"
      >
        <Field label={`Type ${MEMORY_CLEAR_PHRASE} to confirm`} hint="The match must be exact, including capitals and spaces.">
          {(control) => <Input {...control} autoComplete="off" value={typed} onChange={(event) => setTyped(event.target.value)} />}
        </Field>
        {error ? <ErrorState message={error} /> : null}
      </form>
    </Dialog>
  );
}

export interface MemoryHistoryDialogProps {
  item: MemoryItem | null;
  onClose: () => void;
}

/** The recorded changes for one memory, newest first. */
export function MemoryHistoryDialog({ item, onClose }: MemoryHistoryDialogProps) {
  const history = useMemoryHistory(item?.id ?? null);

  return (
    <Dialog open={item !== null} onClose={onClose} title="Memory history" description={item ? item.content : undefined} size="lg">
      {history.isPending ? (
        <SkeletonList rows={2} label="Loading history" />
      ) : history.isError ? (
        <ErrorState message={history.error.message} onRetry={() => void history.refetch()} />
      ) : (history.data ?? []).length === 0 ? (
        <p className="text-sm text-muted">No changes recorded.</p>
      ) : (
        <ol className="flex flex-col gap-3" aria-label="Changes, newest first">
          {(history.data ?? []).map((entry, index) => (
            <li key={`${entry.created_at}-${index}`} className="flex flex-col gap-1 rounded-lg border border-line bg-raised p-3 text-sm">
              <span className="font-medium text-ink">{humanize(entry.action)}</span>
              <span className="text-xs text-muted">{formatDateTime(entry.created_at)}</span>
              {entry.previous_content ? (
                <p className="whitespace-pre-wrap text-muted">
                  <span className="text-xs font-medium">Before: </span>
                  {entry.previous_content}
                </p>
              ) : null}
              {entry.new_content ? (
                <p className="whitespace-pre-wrap text-ink">
                  <span className="text-xs font-medium text-muted">After: </span>
                  {entry.new_content}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}
