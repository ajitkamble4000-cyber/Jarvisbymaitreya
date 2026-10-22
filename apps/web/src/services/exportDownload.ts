/**
 * Downloads files from the API: conversation exports and the task CSV. Each file is fetched with the session cookie, read into a Blob, and
 * saved through a temporary object URL that is revoked afterwards. The file name comes from the
 * server's Content-Disposition header, and only safe characters from it are kept.
 */

import { API_BASE, UNAUTHORIZED_EVENT, errorFromEnvelope, fallbackMessage } from "./api";

export type ExportFormat = "markdown" | "json";

/** How long the object URL stays valid. Long enough for the browser to start the download. */
const REVOKE_DELAY_MS = 1000;

/** Reads filename="..." from a Content-Disposition value. Anything outside letters, digits, dot, space, underscore and dash is removed. */
export function fileNameFromDisposition(header: string | null, fallback: string): string {
  const match = header ? /filename="([^"]+)"/i.exec(header) : null;
  const safe = (match?.[1] ?? "")
    .replace(/[^A-Za-z0-9._ -]/g, "")
    .replace(/^\.+/, "")
    .trim()
    .slice(0, 120);
  return safe || fallback;
}

/** Saves a Blob through an anchor element. The element is removed at once and the URL is revoked later. */
export function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}

/**
 * Fetches a file from the API and saves it. The file name comes from Content-Disposition, or from
 * fallbackName. Resolves with the name used. Failures throw an ApiError with the server's message.
 */
export async function downloadAttachment(path: string, fallbackName: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { credentials: "include", headers: { Accept: "*/*" } });
  } catch {
    throw errorFromEnvelope(0, null);
  }

  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    const body: unknown = await response.json().catch(() => null);
    throw errorFromEnvelope(response.status, body ?? { error: { code: "http_error", message: fallbackMessage(response.status) } });
  }

  const fileName = fileNameFromDisposition(response.headers.get("Content-Disposition"), fallbackName);
  saveBlob(await response.blob(), fileName);
  return fileName;
}

/** Saves a conversation as Markdown or JSON. */
export async function downloadConversationExport(conversationId: string, format: ExportFormat): Promise<string> {
  const path = `/conversations/${encodeURIComponent(conversationId)}/export?format=${format}`;
  return downloadAttachment(path, `conversation.${format === "json" ? "json" : "md"}`);
}

/** Saves the owner's tasks as CSV, with the same status and search filters as the list. "all" and blank mean no filter. */
export async function downloadTasksCsv(filters: { status?: string; q?: string }): Promise<string> {
  const search = new URLSearchParams();
  if (filters.status && filters.status !== "all") search.set("status", filters.status);
  const text = filters.q?.trim() ?? "";
  if (text) search.set("q", text);
  const query = search.toString();
  return downloadAttachment(`/tasks/export.csv${query ? `?${query}` : ""}`, "jarvis-tasks.csv");
}
