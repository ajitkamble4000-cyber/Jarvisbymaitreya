/** Pure helpers for the Memory page: categories, validation, the clear phrase and the export file. */

export type MemoryCategory = "fact" | "preference" | "project" | "task_context" | "conversation_summary";

export const MEMORY_CATEGORIES: ReadonlyArray<{ value: MemoryCategory; label: string }> = [
  { value: "fact", label: "Fact" },
  { value: "preference", label: "Preference" },
  { value: "project", label: "Project" },
  { value: "task_context", label: "Task context" },
  { value: "conversation_summary", label: "Conversation summary" },
];

export const CATEGORY_LABEL: Record<MemoryCategory, string> = {
  fact: "Fact",
  preference: "Preference",
  project: "Project",
  task_context: "Task context",
  conversation_summary: "Conversation summary",
};

/** The exact text the bulk clear needs. The API checks it too. */
export const MEMORY_CLEAR_PHRASE = "DELETE ALL MEMORIES";
export const MEMORY_CONTENT_MAX = 2000;
export const MEMORY_PAGE_SIZE = 50;

export function isClearPhraseMatch(input: string): boolean {
  return input === MEMORY_CLEAR_PHRASE;
}

/** Returns an error message for memory text, or null when it can be saved. */
export function validateMemoryContent(content: string): string | null {
  const trimmed = content.trim();
  if (!trimmed) return "Enter what JARVIS should remember.";
  if (trimmed.length > MEMORY_CONTENT_MAX) return `Use ${MEMORY_CONTENT_MAX} characters or fewer.`;
  return null;
}

export function isMemoryCategory(value: string): value is MemoryCategory {
  return MEMORY_CATEGORIES.some((item) => item.value === value);
}

export function categoryLabel(value: string): string {
  return isMemoryCategory(value) ? CATEGORY_LABEL[value] : value;
}

/** Readable text for each fallback_reason from GET /memories. An unknown reason gets the generic sentence. */
const FALLBACK_REASON_TEXT: Record<string, string> = {
  no_embedding_model: "Semantic search is not set up on the server.",
  invalid_embedding_dimensions: "The server's embedding size is not supported.",
  no_stored_embeddings: "No memory has a search index yet. Build the search index first.",
  provider_not_configured: "Add a Google Gemini key in AI Models to search by meaning.",
  provider_unavailable: "The embedding service could not be reached. Try again later.",
  no_query: "Type a phrase to search by meaning.",
};

/** The sentence beside a fallback code, or null when the request was not a fallback. */
export function fallbackReasonText(reason: string | null): string | null {
  if (!reason) return null;
  return Object.prototype.hasOwnProperty.call(FALLBACK_REASON_TEXT, reason)
    ? FALLBACK_REASON_TEXT[reason]
    : "Keyword search was used instead.";
}

/** Readable text for the reasons that POST /memories/reindex can return with 409 or 503. */
const REINDEX_REASON_TEXT: Record<string, string> = {
  no_embedding_model: "Semantic search is not set up on the server, so no index was built.",
  provider_not_configured: "Add a Google Gemini key in AI Models, then build the index again.",
  invalid_embedding_dimensions: "The server's embedding size is not supported, so no index was built.",
  provider_unavailable: "The embedding service could not be reached. Try again later.",
};

/** Text for a successful reindex call. remaining counts the memories that still need an index. */
export function reindexSuccessText(result: { updated: number; remaining: number }): string {
  const indexed = result.updated === 1 ? "1 memory" : `${result.updated} memories`;
  if (result.remaining === 0) {
    return result.updated === 0 ? "Every memory already has a search index." : `Indexed ${indexed}. Every memory now has a search index.`;
  }
  const left = result.remaining === 1 ? "1 memory still needs" : `${result.remaining} memories still need`;
  return `Indexed ${indexed}. ${left} an index. Build the index again to continue.`;
}

/**
 * Text for a failed reindex call. The API sends error.details with the reason and the counts that were
 * saved before the failure. Unknown details fall back to a generic sentence.
 */
export function reindexErrorText(details: unknown): string {
  if (!details || typeof details !== "object") return "The search index could not be built. Try again later.";
  const record = details as Record<string, unknown>;
  const reason = typeof record.reason === "string" ? record.reason : null;
  const base = reason && Object.prototype.hasOwnProperty.call(REINDEX_REASON_TEXT, reason) ? REINDEX_REASON_TEXT[reason] : "The search index could not be built.";
  const saved = typeof record.updated === "number" && typeof record.remaining === "number" ? ` Saved so far: ${record.updated}. Still without an index: ${record.remaining}.` : "";
  return `${base}${saved}`;
}

/** Downloads a JSON document through a temporary object URL. Browser-only. */
export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
