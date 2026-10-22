import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Icon } from "../../components/ui/Icon";
import { Input, Select } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Switch } from "../../components/ui/Switch";
import { useToast } from "../../components/ui/Toast";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { ApiError } from "../../services/api";
import { formatDateTime, humanize } from "../../services/format";
import { useSettingsQuery, useUpdateSettings } from "../../services/queries";
import {
  MEMORY_CATEGORIES,
  MEMORY_PAGE_SIZE,
  categoryLabel,
  downloadJson,
  fallbackReasonText,
  reindexErrorText,
  reindexSuccessText,
  type MemoryCategory,
} from "./memoryModel";
import { ClearMemoriesDialog, DeleteMemoryDialog, MemoryHistoryDialog } from "./MemoryDialogs";
import { MemoryForm } from "./MemoryForm";
import { exportMemories, useMemoriesQuery, useReindexMemories, type MemoryItem } from "./memoryQueries";

type FormState = { mode: "create" } | { mode: "edit"; item: MemoryItem } | null;

/**
 * Memory: what JARVIS keeps for later replies. Each item can be searched, filtered, edited,
 * deleted and inspected through its history. Bulk clear needs the typed phrase. Search by meaning
 * is optional: it needs an embedding model and a search index, and the page says when it fell back.
 */
export default function MemoryPage() {
  const settings = useSettingsQuery();
  const updateSettings = useUpdateSettings();
  const reindex = useReindexMemories();
  const { toast } = useToast();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<MemoryCategory | "all">("all");
  const [offset, setOffset] = useState(0);
  const [semantic, setSemantic] = useState(false);
  const [indexNote, setIndexNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const debouncedQuery = useDebouncedValue(query, 300);
  const memories = useMemoriesQuery({ query: debouncedQuery, category, offset, semantic });
  const [form, setForm] = useState<FormState>(null);
  const [historyItem, setHistoryItem] = useState<MemoryItem | null>(null);
  const [deleteItem, setDeleteItem] = useState<MemoryItem | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [exporting, setExporting] = useState(false);

  const memoryOn = settings.data?.memory.enabled ?? null;
  const total = memories.data?.total ?? 0;
  const items = memories.data?.items ?? [];
  const filtered = debouncedQuery.trim() !== "" || category !== "all";
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + MEMORY_PAGE_SIZE, total);
  const searchMode = memories.data?.search_mode ?? "keyword";
  const fallbackReason = memories.data?.fallback_reason ?? null;
  const fallbackText = fallbackReasonText(fallbackReason);

  const buildIndex = (): void => {
    setIndexNote(null);
    reindex.mutate(undefined, {
      onSuccess: (result) => {
        const text = reindexSuccessText(result);
        setIndexNote({ tone: "success", text });
        toast({ tone: "success", title: "Search index updated", description: text });
      },
      onError: (cause) => {
        const text = reindexErrorText(cause instanceof ApiError ? cause.details : null);
        setIndexNote({ tone: "error", text });
        toast({ tone: "error", title: "Search index not built", description: text });
      },
    });
  };

  const toggleMemory = (enabled: boolean): void => {
    updateSettings.mutate(
      { memory: { enabled } },
      {
        onSuccess: () => toast({ tone: "success", title: enabled ? "Memory is on" : "Memory is off" }),
        onError: (cause) => toast({ tone: "error", title: "Memory setting not saved", description: cause.message }),
      },
    );
  };

  const exportAll = async (): Promise<void> => {
    setExporting(true);
    try {
      const payload = await exportMemories();
      downloadJson("jarvis-memories.json", payload);
      toast({ tone: "success", title: "Memories exported" });
    } catch (cause) {
      toast({ tone: "error", title: "Export failed", description: cause instanceof ApiError ? cause.message : "Try again." });
    } finally {
      setExporting(false);
    }
  };

  const onFormDone = (message: string): void => {
    setForm(null);
    toast({ tone: "success", title: message });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Memory"
        description="What JARVIS keeps for later replies. You can read, correct or delete any item. Replies use memory only when Include memory is on in AI settings."
        actions={
          <Button variant="primary" onClick={() => setForm({ mode: "create" })}>
            <Icon name="plus" className="size-4" />
            Add memory
          </Button>
        }
      />

      <Card className="flex flex-col gap-3">
        {settings.isPending ? (
          <SkeletonList rows={1} label="Loading memory setting" />
        ) : (
          <Switch
            label="Memory is on"
            description={
              memoryOn === false
                ? "Off: JARVIS does not add or use memories. Existing items stay here until you delete them."
                : "On: JARVIS can save and use memories. Turn it off here at any time."
            }
            checked={memoryOn === true}
            disabled={memoryOn === null || updateSettings.isPending}
            onCheckedChange={toggleMemory}
          />
        )}
        <p className="text-xs text-muted">
          Settings also control whether memories are sent with each reply, in <Link to="/settings?tab=ai" className="text-accent underline underline-offset-4">AI settings</Link>.
        </p>
      </Card>

      <div className="grid gap-4 sm:grid-cols-[1fr_200px_auto] sm:items-end">
        <Field label="Search memories">
          {(control) => (
            <Input
              {...control}
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
            />
          )}
        </Field>
        <Field label="Category">
          {(control) => (
            <Select
              {...control}
              value={category}
              onChange={(event) => {
                setCategory(event.target.value as MemoryCategory | "all");
                setOffset(0);
              }}
            >
              <option value="all">All categories</option>
              {MEMORY_CATEGORIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" loading={exporting} onClick={() => void exportAll()}>
            Export JSON
          </Button>
          <Button variant="danger" onClick={() => setClearOpen(true)} disabled={total === 0 && !filtered}>
            Clear all
          </Button>
        </div>
      </div>

      <Card className="flex flex-col gap-3">
        <Switch
          label="Search by meaning"
          description="Finds memories with a similar meaning, even when the words differ. Needs a Google Gemini key. When this is off, or semantic search is unavailable, the search matches words."
          checked={semantic}
          onCheckedChange={(value) => {
            setSemantic(value);
            setOffset(0);
            setIndexNote(null);
          }}
        />
        {semantic ? (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="secondary" loading={reindex.isPending} onClick={buildIndex}>
                Build search index
              </Button>
              <p className="text-xs text-muted">Each press embeds up to 200 memories that have no index yet.</p>
            </div>
            {indexNote ? (
              <p role={indexNote.tone === "error" ? "alert" : "status"} className={indexNote.tone === "error" ? "text-sm text-danger" : "text-sm text-ink"}>
                {indexNote.text}
              </p>
            ) : null}
          </div>
        ) : null}
      </Card>

      {memories.isPending ? (
        <SkeletonList rows={3} label="Loading memories" />
      ) : memories.isError ? (
        <ErrorState message={memories.error.message} onRetry={() => void memories.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title={filtered ? "No memories match" : "Nothing remembered yet"}
          description={
            filtered
              ? "Try another search or category."
              : "Add a fact or preference here, or ask JARVIS in chat to remember something."
          }
        />
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <p className="text-sm text-muted" aria-live="polite">
              Showing {from} to {to} of {total}
            </p>
            <p className="text-xs text-muted">{`Search mode: ${searchMode}.`}</p>
            {fallbackReason ? <p className="text-xs text-warning">{`Fallback reason: ${fallbackReason}. ${fallbackText ?? ""}`}</p> : null}
          </div>
          <ul aria-label="Memories" className="flex flex-col gap-3">
            {items.map((item) => (
              <li key={item.id} className="flex flex-col gap-3 rounded-card border border-line bg-surface p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <Badge tone="accent">{categoryLabel(item.category)}</Badge>
                  {item.source === "conversation" ? <Badge tone="info">Summary</Badge> : null}
                  <span>{humanize(item.source)}</span>
                  <span>Updated {formatDateTime(item.updated_at)}</span>
                  {item.expires_at ? <span>Expires {formatDateTime(item.expires_at)}</span> : null}
                </div>
                <p className="whitespace-pre-wrap text-sm text-ink">{item.content}</p>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setForm({ mode: "edit", item })}>
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setHistoryItem(item)}>
                    History
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleteItem(item)}>
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          {total > MEMORY_PAGE_SIZE ? (
            <nav aria-label="Memory pages" className="flex items-center justify-between gap-2">
              <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - MEMORY_PAGE_SIZE))}>
                Previous
              </Button>
              <Button variant="secondary" size="sm" disabled={offset + MEMORY_PAGE_SIZE >= total} onClick={() => setOffset(offset + MEMORY_PAGE_SIZE)}>
                Next
              </Button>
            </nav>
          ) : null}
        </>
      )}

      <Dialog
        open={form !== null}
        onClose={() => setForm(null)}
        title={form?.mode === "edit" ? "Edit memory" : "Add memory"}
        description={form?.mode === "edit" ? `Currently ${categoryLabel(form.item.category)}.` : "Write it as you would tell a colleague."}
      >
        {form ? (
          <MemoryForm
            key={form.mode === "edit" ? form.item.id : "create"}
            item={form.mode === "edit" ? form.item : undefined}
            onDone={onFormDone}
            onCancel={() => setForm(null)}
          />
        ) : null}
      </Dialog>

      <MemoryHistoryDialog item={historyItem} onClose={() => setHistoryItem(null)} />
      <DeleteMemoryDialog
        item={deleteItem}
        onClose={() => setDeleteItem(null)}
        onDeleted={() => {
          setDeleteItem(null);
          toast({ tone: "success", title: "Memory deleted" });
        }}
      />
      <ClearMemoriesDialog
        open={clearOpen}
        onClose={() => setClearOpen(false)}
        onCleared={(deleted) => {
          setClearOpen(false);
          setOffset(0);
          toast({ tone: "success", title: `Deleted ${deleted} ${deleted === 1 ? "memory" : "memories"}` });
        }}
      />
    </div>
  );
}
