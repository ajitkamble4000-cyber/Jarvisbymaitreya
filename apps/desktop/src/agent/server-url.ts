/**
 * Server address handling. The agent talks to the server over TLS. Plain http and ws are accepted only for a loopback
 * host, which is how local development runs (docs/desktop-protocol.md, section 8).
 */

export class ServerUrlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ServerUrlError";
  }
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
const WEBSOCKET_PATH = /^\/[A-Za-z0-9/_-]{1,200}$/;

export function isLoopbackHost(hostname: string): boolean {
  return LOOPBACK_HOSTS.has(hostname.toLowerCase());
}

/**
 * Returns the origin of the server (for example https://jarvis.example.com). The address must not carry a path,
 * query, fragment or credentials, because the agent appends its own paths.
 */
export function normaliseServerUrl(input: string): URL {
  const trimmed = input.trim();
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new ServerUrlError("The server address is not a valid URL.");
  }
  if (parsed.username !== "" || parsed.password !== "") {
    throw new ServerUrlError("The server address must not contain a user name or password.");
  }
  if (parsed.search !== "" || parsed.hash !== "") {
    throw new ServerUrlError("The server address must not contain a query or fragment.");
  }
  if (parsed.pathname !== "/" && parsed.pathname !== "") {
    throw new ServerUrlError("Enter the server address without a path, for example https://jarvis.example.com.");
  }
  if (parsed.protocol === "https:") {
    return parsed;
  }
  if (parsed.protocol === "http:" && isLoopbackHost(parsed.hostname)) {
    return parsed;
  }
  throw new ServerUrlError("The server address must use https.");
}

/** https://host + /api/v1/agent/ws becomes wss://host/api/v1/agent/ws (ws:// for loopback hosts only). */
export function websocketUrl(serverUrl: string, websocketPath: string): string {
  if (!WEBSOCKET_PATH.test(websocketPath) || websocketPath.includes("..")) {
    throw new ServerUrlError("The server returned an unsupported websocket path.");
  }
  const origin = normaliseServerUrl(serverUrl);
  const protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${origin.host}${websocketPath}`;
}

export function pairingUrl(serverUrl: string): string {
  const origin = normaliseServerUrl(serverUrl);
  return `${origin.origin}/api/v1/agent/pair`;
}

/** Host name only, for display in the UI. */
export function serverHostForDisplay(serverUrl: string): string | null {
  try {
    return normaliseServerUrl(serverUrl).host;
  } catch {
    return null;
  }
}
