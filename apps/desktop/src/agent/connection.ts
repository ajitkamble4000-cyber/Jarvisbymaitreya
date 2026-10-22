/**
 * WebSocket client for the backend device hub. Handles the handshake (hello within the server's window), heartbeats,
 * close codes (docs/desktop-protocol.md, section 2), reconnects with jittered backoff, and the outbox resend.
 * Commands and cancels are handed to the agent as raw text: verification happens there, not here.
 */
import WebSocket from "ws";
import { parseServerFrame, type AgentResult, type Heartbeat, type Hello } from "../shared/protocol.js";
import type { Outbox } from "./outbox.js";
import { silentLogger, type Logger } from "./logger.js";
import { websocketUrl } from "./server-url.js";

export const CLOSE_CODES = {
  normal: 1000,
  unauthorised: 4401,
  revoked: 4403,
  helloTimeout: 4408,
  superseded: 4409,
} as const;

export const BACKOFF_BASE_MS = 1_000;
export const BACKOFF_MAX_MS = 60_000;
export const DEFAULT_HEARTBEAT_SECONDS = 15;
const HANDSHAKE_TIMEOUT_MS = 10_000;
const MAX_PAYLOAD_BYTES = 256 * 1024;
const RUNNING_IDS_LIMIT = 32;

/**
 * Delay before reconnect attempt number `attempt` (0-based). The capped exponential value is multiplied by a jitter
 * factor in [0.75, 1.0], so the delay never exceeds the cap.
 */
export function backoffDelayMs(
  attempt: number,
  random: () => number = Math.random,
  baseMs: number = BACKOFF_BASE_MS,
  maxMs: number = BACKOFF_MAX_MS,
): number {
  const exponent = Math.min(Math.max(attempt, 0), 30);
  const capped = Math.min(maxMs, baseMs * 2 ** exponent);
  const unit = Math.min(Math.max(random(), 0), 1);
  return Math.min(maxMs, Math.round(capped * (0.75 + 0.25 * unit)));
}

export type ConnectionEvent =
  | { kind: "connecting" }
  | { kind: "connected"; deviceId: string }
  | { kind: "offline"; retryInMs: number; closeCode: number }
  | { kind: "re-pair-required" }
  | { kind: "revoked" }
  | { kind: "replaced" };

export interface ConnectionOptions {
  serverUrl: string;
  websocketPath: string;
  /** Held in memory only. It is the bearer token and the HMAC key. */
  secret: string;
  buildHello: () => Hello;
  outbox: Outbox;
  runningCommandIds: () => string[];
  onCommandFrame: (text: string) => void;
  onCancelFrame: (text: string) => void;
  onEvent: (event: ConnectionEvent) => void;
  logger?: Logger;
  backoff?: { baseMs?: number; maxMs?: number; random?: () => number };
}

export class AgentConnection {
  private readonly options: ConnectionOptions;
  private readonly url: string;
  private readonly logger: Logger;
  private running = false;
  private socket: WebSocket | null = null;
  private connected = false;
  private attempt = 0;
  private heartbeatSeconds = DEFAULT_HEARTBEAT_SECONDS;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private heartbeatTimer: NodeJS.Timeout | null = null;
  /** Command ids whose result was written to the socket on the current connection and not yet covered by a heartbeat. */
  private readonly sentOk = new Set<string>();

  constructor(options: ConnectionOptions) {
    this.options = options;
    this.url = websocketUrl(options.serverUrl, options.websocketPath);
    this.logger = options.logger ?? silentLogger;
  }

  get isConnected(): boolean {
    return this.connected;
  }

  get isRunning(): boolean {
    return this.running;
  }

  start(): void {
    if (this.running) {
      return;
    }
    this.running = true;
    this.openSocket();
  }

  /** Stops reconnecting and closes the socket. Unsent results stay in the outbox. */
  async stop(): Promise<void> {
    this.running = false;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    this.connected = false;
    this.sentOk.clear();
    if (socket === null) {
      return;
    }
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 2_000);
      socket.once("close", () => {
        clearTimeout(timer);
        resolve();
      });
      if (socket.readyState === WebSocket.CONNECTING) {
        socket.terminate();
      } else {
        socket.close(CLOSE_CODES.normal, "agent stopped");
      }
    });
  }

  /** Persists the result, then sends it if the backend connection is up. Otherwise it waits in the outbox. */
  async sendResult(result: AgentResult): Promise<void> {
    await this.options.outbox.add(result);
    const socket = this.socket;
    if (socket === null || !this.connected) {
      return;
    }
    if (await this.sendFrame(socket, JSON.stringify(result))) {
      this.sentOk.add(result.command_id);
    }
  }

  private openSocket(): void {
    this.clearReconnect();
    this.options.onEvent({ kind: "connecting" });
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url, {
        headers: { Authorization: `Bearer ${this.options.secret}` },
        handshakeTimeout: HANDSHAKE_TIMEOUT_MS,
        maxPayload: MAX_PAYLOAD_BYTES,
        perMessageDeflate: false,
      });
    } catch {
      this.logger.warn("Could not start the connection.");
      this.scheduleReconnect(0);
      return;
    }
    this.socket = socket;
    socket.on("open", () => this.onOpen(socket));
    socket.on("message", (data: WebSocket.RawData, isBinary: boolean) => this.onMessage(socket, data, isBinary));
    socket.on("unexpected-response", (_request, response) => {
      if (socket === this.socket) {
        this.logger.warn("The server refused the connection before the upgrade.", { status: response.statusCode ?? 0 });
      }
    });
    socket.on("error", (error: NodeJS.ErrnoException) => {
      this.logger.debug("Connection error.", { code: error.code ?? "unknown" });
    });
    socket.on("close", (code: number) => this.onClose(socket, code));
  }

  private onOpen(socket: WebSocket): void {
    if (socket !== this.socket) {
      return;
    }
    // hello must arrive within 10 seconds of the handshake, so it goes out immediately.
    void this.sendFrame(socket, JSON.stringify(this.options.buildHello()));
  }

  private onMessage(socket: WebSocket, data: WebSocket.RawData, isBinary: boolean): void {
    if (socket !== this.socket) {
      return;
    }
    if (isBinary) {
      this.logger.warn("Ignored a binary frame from the server.");
      return;
    }
    const text = rawToText(data);
    const frame = parseServerFrame(text);
    switch (frame.kind) {
      case "hello_ack":
        this.onHelloAck(socket, frame.message.device_id, frame.message.heartbeat_interval_seconds);
        return;
      case "command":
        this.options.onCommandFrame(text);
        return;
      case "cancel":
        this.options.onCancelFrame(text);
        return;
      case "ping":
        void this.sendFrame(socket, JSON.stringify({ type: "pong", nonce: frame.message.nonce }));
        return;
      case "error":
        this.logger.warn("The server reported a protocol error.", { code: frame.message.code });
        return;
      case "unknown":
        this.logger.debug("Ignored an unknown server frame.", { type: frame.type });
        return;
      case "invalid":
        this.logger.warn("Ignored a malformed server frame.", { reason: frame.reason });
        return;
    }
  }

  private onHelloAck(socket: WebSocket, deviceId: string, heartbeatSeconds: number): void {
    this.connected = true;
    this.attempt = 0;
    this.heartbeatSeconds = heartbeatSeconds;
    this.options.onEvent({ kind: "connected", deviceId });
    this.startHeartbeat(socket);
    this.runInBackground(this.resendOutbox(socket), "outbox resend");
  }

  /** Re-sends everything still in the outbox. Entries whose send completed are acknowledged. */
  private async resendOutbox(socket: WebSocket): Promise<void> {
    const acknowledged: string[] = [];
    for (const result of this.options.outbox.pending()) {
      if (socket !== this.socket) {
        return;
      }
      if (await this.sendFrame(socket, JSON.stringify(result))) {
        acknowledged.push(result.command_id);
      } else {
        break;
      }
    }
    if (acknowledged.length > 0 && socket === this.socket) {
      await this.options.outbox.acknowledge(acknowledged);
    }
  }

  private startHeartbeat(socket: WebSocket): void {
    this.clearHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.runInBackground(this.sendHeartbeat(socket), "heartbeat");
    }, this.heartbeatSeconds * 1000);
  }

  private async sendHeartbeat(socket: WebSocket): Promise<void> {
    if (socket !== this.socket || !this.connected) {
      return;
    }
    const covered = [...this.sentOk];
    const heartbeat: Heartbeat = {
      type: "heartbeat",
      running_command_ids: this.options.runningCommandIds().slice(0, RUNNING_IDS_LIMIT),
      sent_at: new Date().toISOString(),
    };
    if (await this.sendFrame(socket, JSON.stringify(heartbeat))) {
      // The results sent before this heartbeat went out without error, so they leave the outbox.
      for (const id of covered) {
        this.sentOk.delete(id);
      }
      await this.options.outbox.acknowledge(covered);
    }
  }

  private onClose(socket: WebSocket, code: number): void {
    if (socket !== this.socket) {
      return;
    }
    this.socket = null;
    this.connected = false;
    this.clearHeartbeat();
    this.sentOk.clear();
    if (!this.running) {
      return;
    }
    switch (code) {
      case CLOSE_CODES.unauthorised:
        this.running = false;
        this.options.onEvent({ kind: "re-pair-required" });
        return;
      case CLOSE_CODES.revoked:
        this.running = false;
        this.options.onEvent({ kind: "revoked" });
        return;
      case CLOSE_CODES.superseded:
        this.running = false;
        this.options.onEvent({ kind: "replaced" });
        return;
      default:
        // Normal closes, hello timeouts (4408), network drops and abnormal closes all reconnect with backoff.
        this.scheduleReconnect(code);
    }
  }

  private scheduleReconnect(closeCode: number): void {
    const delay = backoffDelayMs(
      this.attempt,
      this.options.backoff?.random ?? Math.random,
      this.options.backoff?.baseMs ?? BACKOFF_BASE_MS,
      this.options.backoff?.maxMs ?? BACKOFF_MAX_MS,
    );
    this.attempt += 1;
    this.options.onEvent({ kind: "offline", retryInMs: delay, closeCode });
    this.clearReconnect();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.running) {
        this.openSocket();
      }
    }, delay);
  }

  private async sendFrame(socket: WebSocket, text: string): Promise<boolean> {
    if (socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    // ws reports success with a null or undefined error depending on the path, so any falsy value counts as success.
    return new Promise<boolean>((resolve) => {
      socket.send(text, (error?: Error | null) => resolve(!error));
    });
  }

  /** Background work must never surface as an unhandled rejection: a failed write is logged and the next cycle retries. */
  private runInBackground(task: Promise<unknown>, label: string): void {
    task.catch((error: unknown) => {
      this.logger.error("A background task failed.", {
        task: label,
        errorName: error instanceof Error ? error.name : "unknown",
      });
    });
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private clearTimers(): void {
    this.clearReconnect();
    this.clearHeartbeat();
  }
}

function rawToText(data: WebSocket.RawData): string {
  if (Buffer.isBuffer(data)) {
    return data.toString("utf8");
  }
  if (Array.isArray(data)) {
    return Buffer.concat(data).toString("utf8");
  }
  return Buffer.from(data).toString("utf8");
}
