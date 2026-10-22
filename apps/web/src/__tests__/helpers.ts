import { vi } from "vitest";

/** Builds a fetch mock that answers by pathname (without the /api/v1 prefix and query string). */
export function routeFetch(routes: Record<string, (url: URL, init?: RequestInit) => unknown>) {
  const calls: string[] = [];
  const mock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(raw, "http://localhost");
    calls.push(`${url.pathname.replace(/^\/api\/v1/, "")}${url.search}`);
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const handler = routes[path];
    if (!handler) return new Response(JSON.stringify({ error: { code: "not_found", message: "Not found." } }), { status: 404 });
    const body = handler(url, init);
    if (body === undefined) return new Response(null, { status: 204 });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", mock);
  return { mock, calls };
}

export function taskFixture(id: string, status: string, title = `Task ${id}`) {
  return {
    id,
    title,
    description: "",
    source: "chat",
    tool_name: "launch_app",
    arguments: {},
    risk_level: "medium",
    status,
    device: null,
    conversation_id: null,
    required_permissions: [],
    current_step: null,
    progress: null,
    result: null,
    error: null,
    attempt: 0,
    max_attempts: 1,
    approval_expires_at: null,
    created_at: "2026-10-19T04:00:00Z",
    updated_at: "2026-10-19T04:00:00Z",
    started_at: null,
    finished_at: null,
    actions: {
      approve: status === "awaiting_approval",
      deny: status === "awaiting_approval",
      cancel: status === "running" || status === "queued",
      retry: status === "failed",
    },
  };
}
