import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, UNAUTHORIZED_EVENT, api, csrfHeaders, errorFromEnvelope, readCookie } from "../services/api";

describe("cookie and CSRF helpers", () => {
  it("reads a named cookie from a cookie header string", () => {
    expect(readCookie("jarvis_csrf", "a=1; jarvis_csrf=abc123; other=x")).toBe("abc123");
    expect(readCookie("missing", "a=1")).toBeNull();
  });

  it("sends X-CSRF-Token only on unsafe methods", () => {
    const cookie = "jarvis_csrf=token-1";
    expect(csrfHeaders("POST", cookie)).toEqual({ "X-CSRF-Token": "token-1" });
    expect(csrfHeaders("patch", cookie)).toEqual({ "X-CSRF-Token": "token-1" });
    expect(csrfHeaders("DELETE", cookie)).toEqual({ "X-CSRF-Token": "token-1" });
    expect(csrfHeaders("GET", cookie)).toEqual({});
  });

  it("omits the CSRF header when the cookie is absent", () => {
    expect(csrfHeaders("POST", "session=1")).toEqual({});
  });
});

describe("error envelope mapping", () => {
  it("maps the error envelope to an ApiError with code, message, details and request id", () => {
    const error = errorFromEnvelope(409, {
      error: { code: "not_configured", message: "Add a key first.", details: { provider: "gemini" } },
      request_id: "req-1",
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe("not_configured");
    expect(error.message).toBe("Add a key first.");
    expect(error.details).toEqual({ provider: "gemini" });
    expect(error.requestId).toBe("req-1");
  });

  it("falls back to a safe message when the body is not an envelope", () => {
    const error = errorFromEnvelope(502, "<html>bad gateway</html>");
    expect(error.code).toBe("http_error");
    expect(error.message).toBe("The server could not complete the request.");
  });
});

describe("request()", () => {
  beforeEach(() => {
    document.cookie = "jarvis_csrf=csrf-value; path=/";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "jarvis_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  function mockFetch(response: () => Response | Promise<Response>) {
    const fetchMock = vi.fn(async () => response());
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("sends credentials, the CSRF header and a JSON body on POST", async () => {
    const fetchMock = mockFetch(() => new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const result = await api.post<{ ok: boolean }>("/tasks/1/deny", { reason: "not now" });
    expect(result).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/tasks/1/deny");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf-value");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({ reason: "not now" });
  });

  it("does not send the CSRF header on GET", async () => {
    const fetchMock = mockFetch(() => new Response("{}", { status: 200 }));
    await api.get("/health");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers as Record<string, string>).not.toHaveProperty("X-CSRF-Token");
  });

  it("rejects with the mapped ApiError on an error envelope", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: { code: "invalid_credentials", message: "Wrong password." } }), { status: 401 }));
    await expect(api.post("/auth/login", { email: "a", password: "b" })).rejects.toMatchObject({ code: "invalid_credentials", status: 401, message: "Wrong password." });
  });

  it("reports an unreachable server as network_error with status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api.get("/health")).rejects.toMatchObject({ code: "network_error", status: 0 });
  });

  it("resolves undefined for 204 responses", async () => {
    mockFetch(() => new Response(null, { status: 204 }));
    await expect(api.del("/schedules/1")).resolves.toBeUndefined();
  });

  it("announces a 401 on a non-auth request so the auth gate can re-check", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: { code: "unauthorized", message: "Sign in." } }), { status: 401 }));
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);
    try {
      await expect(api.get("/tasks")).rejects.toBeInstanceOf(ApiError);
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(UNAUTHORIZED_EVENT, listener);
    }
  });
});
