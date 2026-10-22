import { describe, expect, it } from "vitest";
import { pairWithServer, PairingError, type PairingRequestInput } from "../src/agent/pairing.js";
import { websocketUrl, normaliseServerUrl, pairingUrl, ServerUrlError, isLoopbackHost } from "../src/agent/server-url.js";
import { parseServerFrame } from "../src/shared/protocol.js";
import { SERVER_PAIR_PATH, TEST_SECRET } from "./helpers.js";

const VALID_RESPONSE = {
  device_id: "11111111-2222-4333-8444-555555555555",
  device_secret: TEST_SECRET,
  websocket_path: "/api/v1/agent/ws",
  heartbeat_interval_seconds: 15,
  protocol_version: 1,
};

function input(overrides: Partial<PairingRequestInput> = {}): PairingRequestInput {
  return {
    serverUrl: "http://127.0.0.1:8000",
    code: "KQ7M-2XBV",
    deviceName: "Test PC",
    deviceType: "laptop",
    os: "simulated",
    osVersion: "0.0.0-simulated",
    agentVersion: "1.0.0",
    capabilities: ["launch_app", "system_info"],
    resources: {
      apps: [{ id: "chrome", label: "Google Chrome" }],
      urls: [],
      folders: [],
      projects: [],
      files: [],
      checks: [],
    },
    ...overrides,
  };
}

function fakeFetch(respond: () => Response | Promise<Response>): { fetchImpl: typeof fetch; calls: Array<{ url: string; body: string }> } {
  const calls: Array<{ url: string; body: string }> = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), body: String(init?.body ?? "") });
    return respond();
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("pairWithServer", () => {
  it("posts the description to /api/v1/agent/pair and returns the validated response", async () => {
    const { fetchImpl, calls } = fakeFetch(() => json(201, VALID_RESPONSE));
    const result = await pairWithServer(input({ fetchImpl }));
    expect(result).toEqual(VALID_RESPONSE);
    expect(calls[0]?.url).toBe(`http://127.0.0.1:8000${SERVER_PAIR_PATH}`);
    const body = JSON.parse(calls[0]?.body ?? "{}") as Record<string, unknown>;
    expect(body).toMatchObject({ code: "KQ7M-2XBV", device_name: "Test PC", os: "simulated" });
    expect(Object.keys(body).sort()).toEqual(
      ["agent_version", "capabilities", "code", "device_name", "device_type", "os", "os_version", "resources"].sort(),
    );
  });

  it("maps a 401 to a message that never includes the code", async () => {
    const { fetchImpl } = fakeFetch(() => json(401, { detail: "KQ7M-2XBV is wrong" }));
    const attempt = pairWithServer(input({ fetchImpl }));
    await expect(attempt).rejects.toBeInstanceOf(PairingError);
    await expect(attempt).rejects.toMatchObject({ status: 401 });
    await expect(attempt).rejects.toThrow(/invalid or has expired/);
    await expect(attempt).rejects.not.toThrow(/KQ7M-2XBV/);
  });

  it("maps 429 to a wait-and-retry message", async () => {
    const { fetchImpl } = fakeFetch(() => json(429, {}));
    await expect(pairWithServer(input({ fetchImpl }))).rejects.toThrow(/Too many pairing attempts/);
  });

  it("maps other HTTP errors to a short status message without the response body", async () => {
    const { fetchImpl } = fakeFetch(() => new Response("<html>internal details</html>", { status: 500 }));
    const error = await pairWithServer(input({ fetchImpl })).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(PairingError);
    expect((error as Error).message).toBe("The server returned an error (HTTP 500).");
  });

  it("reports a network failure as unreachable", async () => {
    const { fetchImpl } = fakeFetch(() => {
      throw new TypeError("fetch failed");
    });
    await expect(pairWithServer(input({ fetchImpl }))).rejects.toThrow(/Could not reach the JARVIS server/);
  });

  it("rejects a response that does not match the contract", async () => {
    const { fetchImpl } = fakeFetch(() => json(201, { device_id: "x" }));
    await expect(pairWithServer(input({ fetchImpl }))).rejects.toThrow(/not recognised/);
  });

  it("rejects an unsupported protocol version", async () => {
    const { fetchImpl } = fakeFetch(() => json(201, { ...VALID_RESPONSE, protocol_version: 2 }));
    await expect(pairWithServer(input({ fetchImpl }))).rejects.toThrow(/protocol version/);
  });

  it("refuses a plain http server that is not loopback, before any request is made", async () => {
    const { fetchImpl, calls } = fakeFetch(() => json(201, VALID_RESPONSE));
    await expect(pairWithServer(input({ fetchImpl, serverUrl: "http://jarvis.example.com" }))).rejects.toBeInstanceOf(
      PairingError,
    );
    expect(calls).toHaveLength(0);
  });
});

describe("server address handling", () => {
  it("accepts https origins and loopback http origins", () => {
    expect(normaliseServerUrl("https://jarvis.example.com/").origin).toBe("https://jarvis.example.com");
    expect(normaliseServerUrl("http://127.0.0.1:8000").origin).toBe("http://127.0.0.1:8000");
    expect(normaliseServerUrl("http://localhost:8000").origin).toBe("http://localhost:8000");
  });

  it("rejects paths, queries, credentials and non-http schemes", () => {
    expect(() => normaliseServerUrl("https://jarvis.example.com/app")).toThrow(ServerUrlError);
    expect(() => normaliseServerUrl("https://jarvis.example.com/?x=1")).toThrow(ServerUrlError);
    expect(() => normaliseServerUrl("https://user:pass@jarvis.example.com")).toThrow(ServerUrlError);
    expect(() => normaliseServerUrl("file:///C:/x")).toThrow(ServerUrlError);
    expect(() => normaliseServerUrl("not a url")).toThrow(ServerUrlError);
  });

  it("derives wss for https and ws only for loopback http", () => {
    expect(websocketUrl("https://jarvis.example.com", "/api/v1/agent/ws")).toBe(
      "wss://jarvis.example.com/api/v1/agent/ws",
    );
    expect(websocketUrl("http://127.0.0.1:8000", "/api/v1/agent/ws")).toBe("ws://127.0.0.1:8000/api/v1/agent/ws");
  });

  it("rejects a websocket path with traversal or unexpected characters", () => {
    expect(() => websocketUrl("https://a.example.com", "/api/../etc")).toThrow(ServerUrlError);
    expect(() => websocketUrl("https://a.example.com", "//evil.example.com/ws")).toThrow(ServerUrlError);
    expect(() => websocketUrl("https://a.example.com", "/ws?x=1")).toThrow(ServerUrlError);
  });

  it("builds the pairing URL from the origin", () => {
    expect(pairingUrl("https://jarvis.example.com")).toBe("https://jarvis.example.com/api/v1/agent/pair");
  });

  it("recognises loopback host names", () => {
    expect(isLoopbackHost("127.0.0.1")).toBe(true);
    expect(isLoopbackHost("LOCALHOST")).toBe(true);
    expect(isLoopbackHost("example.com")).toBe(false);
  });
});

describe("parseServerFrame", () => {
  it("classifies known server frames", () => {
    const ack = parseServerFrame(
      JSON.stringify({ type: "hello_ack", device_id: "d1", server_time: "2026-10-19T04:51:37Z", heartbeat_interval_seconds: 15, protocol_version: 1 }),
    );
    expect(ack.kind).toBe("hello_ack");
    expect(parseServerFrame(JSON.stringify({ type: "ping", nonce: "n" })).kind).toBe("ping");
  });

  it("reports unknown types for logging and invalid frames for dropping", () => {
    expect(parseServerFrame(JSON.stringify({ type: "from_the_future" }))).toEqual({ kind: "unknown", type: "from_the_future" });
    expect(parseServerFrame("nope").kind).toBe("invalid");
    expect(parseServerFrame("[1,2]").kind).toBe("invalid");
    expect(parseServerFrame(JSON.stringify({ type: "command", id: "x" })).kind).toBe("invalid");
  });
});
