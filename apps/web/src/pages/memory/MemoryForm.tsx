import { useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Select, Textarea } from "../../components/ui/Input";
import { ApiError } from "../../services/api";
import { MEMORY_CATEGORIES, MEMORY_CONTENT_MAX, categoryLabel, isMemoryCategory, validateMemoryContent, type MemoryCategory } from "./memoryModel";
import { useCreateMemory, useUpdateMemory, type MemoryItem } from "./memoryQueries";

export interface MemoryFormProps {
  /** Present when editing. Absent when adding. */
  item?: MemoryItem;
  onDone: (message: string) => void;
  onCancel: () => void;
}

function describeFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return "The memory could not be saved.";
  if (error.code === "sensitive_content") return "This looks like a password, key or other credential, so JARVIS will not store it.";
  if (error.code === "memory_disabled") return "Memory is off. Turn it on above, then try again.";
  return error.message;
}

/** Add or edit one memory. Content is checked before the request and the server's reason is shown after it. */
export function MemoryForm({ item, onDone, onCancel }: MemoryFormProps) {
  const create = useCreateMemory();
  const update = useUpdateMemory();
  const editing = item !== undefined;
  const [category, setCategory] = useState<MemoryCategory>(() => {
    const current = item?.category ?? "fact";
    return isMemoryCategory(current) ? current : "fact";
  });
  const [content, setContent] = useState(item?.content ?? "");
  const [contentError, setContentError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const pending = create.isPending || update.isPending;

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitError(null);
    const problem = validateMemoryContent(content);
    setContentError(problem);
    if (problem) return;
    const trimmed = content.trim();

    if (editing && item) {
      const input: { content?: string; category?: MemoryCategory } = {};
      if (trimmed !== item.content) input.content = trimmed;
      if (category !== item.category) input.category = category;
      if (Object.keys(input).length === 0) {
        onDone("No changes to save");
        return;
      }
      update.mutate(
        { id: item.id, input },
        {
          onSuccess: () => onDone("Memory updated"),
          onError: (cause) => setSubmitError(describeFailure(cause)),
        },
      );
      return;
    }

    create.mutate(
      { category, content: trimmed },
      {
        onSuccess: () => onDone("Memory added"),
        onError: (cause) => setSubmitError(describeFailure(cause)),
      },
    );
  };

  return (
    <form noValidate onSubmit={onSubmit} aria-label={editing ? "Edit memory" : "Add memory"} className="flex flex-col gap-4">
      {submitError ? <ErrorState title="Not saved" message={submitError} /> : null}

      <Field label="Category">
        {(control) => (
          <Select {...control} value={category} onChange={(event) => setCategory(event.target.value as MemoryCategory)}>
            {MEMORY_CATEGORIES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        )}
      </Field>

      <Field
        label="What JARVIS should remember"
        hint={`Up to ${MEMORY_CONTENT_MAX} characters. Do not include passwords or keys.`}
        error={contentError}
      >
        {(control) => (
          <Textarea
            {...control}
            rows={5}
            required
            invalid={Boolean(contentError)}
            value={content}
            maxLength={MEMORY_CONTENT_MAX + 200}
            onChange={(event) => setContent(event.target.value)}
          />
        )}
      </Field>

      <p className="text-xs text-muted">Current category: {categoryLabel(category)}.</p>

      <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={pending}>
          {editing ? "Save memory" : "Add memory"}
        </Button>
      </div>
    </form>
  );
}
