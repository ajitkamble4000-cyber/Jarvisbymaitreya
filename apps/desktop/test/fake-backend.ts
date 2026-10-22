/**
 * Test double for the backend device hub. It speaks the agent side of docs/desktop-protocol.md over a real ws server
 * on 127.0.0.1. It lives in test/ only.
 */
import type { IncomingMessage } from "node:http";
import { AddressInfo } from "node:net";
import { WebSocket, WebSocketServer } from "ws";

export interface FakeConnection {
  index: number;
  socket: WebSocket;
  authorization: string | undefined;
  path: string | undefined;
  received: string[];
  closeCode: number | null;
}

export interface FakeBackend {
  url: string;
  port: number;
  connections: FakeConnection[];
  /** The newest connection, or undefined. */
  current(): FakeConnection | undefined;
  /** Frames of a given type received on the newest connection. */
  framesOf(type: string, connection?: FakeConnection): Record<string, unknown>[];
  send(connection: FakeConnection, frame: unknown): void;
  /** Sends a close frame with the given protocol code. */
  closeWith(connection: FakeConnection, code: number, reason?: string): void;
  /** Sends hello_ack after the agent's hello. Set automatically unless autoAck is false. */
  ackHelloOn(connection: FakeConnection, heartbeatSeconds?: number): void;
  close(): Promise<void>;
}

export interface FakeBackendOptions {
  autoAck?: boolean;
  heartbeatSeconds?: number;
}

export async function startFakeBackend(options: FakeBackendOptions = {}): Promise<FakeBackend> {
  const autoAck = options.autoAck ?? true;
  const heartbeatSeconds = options.heartbeatSeconds ?? 15;
  const server = new WebSocketServer({ host: "127.0.0.1", port: 0, path: "/api/v1/agent/ws" });
  const connections: FakeConnection[] = [];
  const started = new Promise<void>((resolve) => server.once("listening", () => resolve()));
  await started;
  const address = server.address() as AddressInfo;

  server.on("connection", (socket: WebSocket, request: IncomingMessage) => {
    const connection: FakeConnection = {
      index: connections.length,
      socket,
      authorization: request.headers.authorization,
      path: request.url,
      received: [],
      closeCode: null,
    };
    connections.push(connection);
    socket.on("message", (data: Buffer) => {
      const text = data.toString("utf8");
      connection.received.push(text);
      const frame = JSON.parse(text) as { type?: string; device_id?: string };
      if (frame.type === "hello" && autoAck) {
        socket.send(
          JSON.stringify({
            type: "hello_ack",
            device_id: "11111111-2222-4333-8444-555555555555",
            server_time: new Date().toISOString(),
            heartbeat_interval_seconds: heartbeatSeconds,
            protocol_version: 1,
          }),
        );
      }
    });
    socket.on("close", (code: number) => {
      connection.closeCode = code;
    });
  });

  const backend: FakeBackend = {
    url: `http://127.0.0.1:${address.port}`,
    port: address.port,
    connections,
    current: () => connections[connections.length - 1],
    framesOf: (type, connection) => {
      const target = connection ?? connections[connections.length - 1];
      if (target === undefined) {
        return [];
      }
      return target.received
        .map((text) => JSON.parse(text) as Record<string, unknown>)
        .filter((frame) => frame.type === type);
    },
    send: (connection, frame) => {
      connection.socket.send(JSON.stringify(frame));
    },
    closeWith: (connection, code, reason = "") => {
      connection.socket.close(code, reason);
    },
    ackHelloOn: (connection, seconds = heartbeatSeconds) => {
      connection.socket.send(
        JSON.stringify({
          type: "hello_ack",
          device_id: "11111111-2222-4333-8444-555555555555",
          server_time: new Date().toISOString(),
          heartbeat_interval_seconds: seconds,
          protocol_version: 1,
        }),
      );
    },
    close: () =>
      new Promise<void>((resolve) => {
        for (const client of server.clients) {
          client.terminate();
        }
        server.close(() => resolve());
      }),
  };
  return backend;
}
