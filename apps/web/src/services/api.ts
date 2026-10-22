/**
 * Typed client for the JARVIS REST and SSE contract (docs/api.md).
 *
 * - Requests always include credentials (the jarvis_session cookie).
 * - Unsafe methods send X-CSRF-Token copied from the readable jarvis_csrf cookie.
 * - Error envelopes {error: {code, message, details}, request_id} become ApiError.
 * - POST SSE endpoints are read with streamSSE, which yields {event, data} pairs.
 * - The /events/stream signal channel is handled by connectEventStream with reconnection.
 */

import type { LiveEvent } from "../types/api";

export const API_BASE = "/api/v1";
export const CSRF_COOKIE = "jarvis_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
/** Dispatched on window when a non-auth request returns 401, so the auth gate can re-check. */
export const UNAUTHORIZED_EVENT = "jarvis:unauthorized";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  details?: unknown;
  requestId?: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
  }
}

/** Reads one cookie value from a cookie header string. Pure, so it can be tested. */
export function readCookie(name: string, cookieString: string = currentCookieString()): string | null {
  for (const part of cookieString.split(";")) {
    const trimmed = part.trim();
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    if (trimmed.slice(0, eq) === name) {
      const raw = trimmed.slice(eq + 1);
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  return null;
}

function currentCookieString(): string {
  return typeof document === "undefined" ? "" : document.cookie;
}

/** Returns the CSRF header for unsafe methods when the csrf cookie is present. */
export function csrfHeaders(method: string, cookieString?: string): Record<string, string> {
  if (!UNSAFE_METHODS.has(method.toUpperCase())) return {};
  const token = readCookie(CSRF_COOKIE, cookieString);
  return token ? { [CSRF_HEADER]: token } : {};
}

export function fallbackMessage(status: number): string {
  if (status === 0) return "The server could not be reached. Check that JARVIS is running.";
  if (status === 401) return "Your session has ended. Sign in again.";
  if (status === 403) return "You do not have permission for this action.";
  if (status === 404) return "That item could not be found.";
  if (status === 409) return "This action conflicts with the current state.";
  if (status === 422) return "Some values were rejected. Check the form and try again.";
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  if (status >= 500) return "The server could not complete the request.";
  return "The request failed.";
}

/** Maps a non-2xx response body to an ApiError. Tolerates bodies that are not JSON. */
export function errorFromEnvelope(status: number, body: unknown): ApiError {
  const record = isRecord(body) ? body : null;
  const requestId = record && typeof record.request_id === "string" ? record.request_id : null;
  const envelope = record && isRecord(record.error) ? record.error : null;
  if (envelope) {
    const code = typeof envelope.code === "string" ? envelope.code : "http_error";
    const message = typeof envelope.message === "string" && envelope.message ? envelope.message : fallbackMessage(status);
    return new ApiError({ status, code, message, details: envelope.details ?? null, requestId });
  }
  return new ApiError({ status, code: "http_error", message: fallbackMessage(status), requestId });
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export interface RequestOptions {
  signal?: AbortSignal;
}

async function parseJsonSafely(response: Response): Promise<unknown> {
  const text = await response.text().catch(() => "");
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

/** Performs one JSON request against /api/v1. Resolves undefined for 204 responses. */
export async function request<T>(method: HttpMethod, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...csrfHeaders(method) };
  const init: RequestInit = { method, credentials: "include", headers, signal: options.signal };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    throw new ApiError({ status: 0, code: "network_error", message: fallbackMessage(0) });
  }

  if (response.status === 204) return undefined as T;

  const payload = await parseJsonSafely(response);
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/") && typeof window !== "undefined") {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw errorFromEnvelope(response.status, payload);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>("GET", path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>("POST", path, body, options),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>("PUT", path, body, options),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>("PATCH", path, body, options),
  del: <T = void>(path: string, body?: unknown, options?: RequestOptions) => request<T>("DELETE", path, body, options),
};

/** Builds a query string, dropping undefined, null and empty values. */
export function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// ---------------------------------------------------------------- SSE

export interface SSEEvent {
  event: string;
  data: string;
}

export interface SSEParser {
  /** Feeds a decoded text chunk. Complete events are passed to the callback. */
  feed(chunk: string): void;
  /** Ends the stream. An incomplete event (no blank line) is discarded, as the spec requires. */
  end(): void;
}

/**
 * Incremental text/event-stream parser. Handles CRLF and CR line endings, comment lines,
 * multi-line data (joined with \n), default event name "message", and chunks that split lines.
 */
export function createSSEParser(onEvent: (event: SSEEvent) => void): SSEParser {
  let buffer = "";
  let pendingCarriageReturn = false;
  let eventName = "";
  let dataLines: string[] = [];

  const dispatch = (): void => {
    if (dataLines.length > 0) {
      onEvent({ event: eventName || "message", data: dataLines.join("\n") });
    }
    eventName = "";
    dataLines = [];
  };

  const processLine = (line: string): void => {
    if (line === "") {
      dispatch();
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
  };

  return {
    feed(chunk: string): void {
      let text = pendingCarriageReturn ? `\r${chunk}` : chunk;
      pendingCarriageReturn = false;
      // A trailing CR may be the first half of CRLF split across chunks; wait for the next chunk.
      if (text.endsWith("\r")) {
        pendingCarriageReturn = true;
        text = text.slice(0, -1);
      }
      buffer += text.replace(/\r\n?/g, "\n");
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        processLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    },
    end(): void {
      buffer = "";
      pendingCarriageReturn = false;
      eventName = "";
      dataLines = [];
    },
  };
}

/** Reads a streamed response body and yields each complete SSE event. */
export async function* readSSE(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SSEEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const ready: SSEEvent[] = [];
  const parser = createSSEParser((event) => ready.push(event));
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
      while (ready.length > 0) {
        const next = ready.shift();
        if (next) yield next;
      }
    }
    parser.feed(decoder.decode());
    parser.end();
    while (ready.length > 0) {
      const next = ready.shift();
      if (next) yield next;
    }
  } catch (error) {
    if (signal?.aborted || isAbortError(error)) return;
    throw error;
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

/** POSTs a JSON body to an SSE endpoint and yields its events. Aborting ends the iterator quietly. */
export async function* streamSSE(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<SSEEvent> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
    ...csrfHeaders("POST"),
  };
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      headers,
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (cause) {
    if (isAbortError(cause) || signal?.aborted) return;
    throw new ApiError({ status: 0, code: "network_error", message: fallbackMessage(0) });
  }

  if (!response.ok) {
    const payload = await parseJsonSafely(response);
    throw errorFromEnvelope(response.status, payload);
  }
  if (!response.body) {
    throw new ApiError({ status: response.status, code: "stream_unavailable", message: "The server did not return a stream." });
  }
  yield* readSSE(response.body, signal);
}

// ---------------------------------------------------------------- events stream

export const LIVE_EVENT_NAMES = [
  "task.updated",
  "approval.requested",
  "device.updated",
  "schedule.updated",
  "chat.message",
  "notification.created",
  "heartbeat",
] as const;

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface EventStreamOptions {
  url?: string;
  onStatusChange?: (status: ConnectionStatus) => void;
  /** Maximum delay between reconnect attempts, in milliseconds. */
  maxBackoffMs?: number;
}

/** Parses one named event payload. Returns null for unknown names or malformed JSON. */
export function parseLiveEvent(name: string, raw: string): LiveEvent | null {
  if (!(LIVE_EVENT_NAMES as readonly string[]).includes(name)) return null;
  try {
    const data = JSON.parse(raw) as unknown;
    if (typeof data !== "object" || data === null) return null;
    return { type: name, data } as LiveEvent;
  } catch {
    return null;
  }
}

/**
 * Subscribes to /events/stream. EventSource reconnects on its own for transient errors; when it
 * gives up (CLOSED), this helper reconnects with exponential backoff. Returns a close function.
 */
export function connectEventStream(onEvent: (event: LiveEvent) => void, options: EventStreamOptions = {}): () => void {
  const url = options.url ?? `${API_BASE}/events/stream`;
  const maxBackoff = options.maxBackoffMs ?? 30_000;
  const setStatus = (status: ConnectionStatus): void => options.onStatusChange?.(status);
  let source: EventSource | null = null;
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const open = (): void => {
    if (closed || typeof EventSource === "undefined") {
      setStatus("closed");
      return;
    }
    setStatus(attempt === 0 ? "connecting" : "reconnecting");
    const current = new EventSource(url, { withCredentials: true });
    source = current;
    current.onopen = () => {
      attempt = 0;
      setStatus("open");
    };
    const handle = (name: string) => (message: MessageEvent<string>) => {
      const event = parseLiveEvent(name, message.data);
      if (event) onEvent(event);
    };
    for (const name of LIVE_EVENT_NAMES) {
      current.addEventListener(name, handle(name) as EventListener);
    }
    current.onerror = () => {
      if (closed) return;
      if (current.readyState === EventSource.CLOSED) {
        current.close();
        scheduleReconnect();
      } else {
        setStatus("reconnecting");
      }
    };
  };

  const scheduleReconnect = (): void => {
    if (closed) return;
    attempt += 1;
    const delay = Math.min(maxBackoff, 1000 * 2 ** Math.min(attempt, 10));
    setStatus("reconnecting");
    timer = setTimeout(open, delay);
  };

  open();

  return () => {
    closed = true;
    if (timer !== undefined) clearTimeout(timer);
    source?.close();
    source = null;
    setStatus("closed");
  };
}
