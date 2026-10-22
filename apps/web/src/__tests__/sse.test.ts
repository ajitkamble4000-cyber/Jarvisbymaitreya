import { describe, expect, it } from "vitest";
import { createSSEParser, parseLiveEvent, readSSE, type SSEEvent } from "../services/api";
import { parseChatEvent, collectAssistantText, type ChatStreamEvent } from "../services/chatStream";

function collect(chunks: string[], end = true): SSEEvent[] {
  const events: SSEEvent[] = [];
  const parser = createSSEParser((event) => events.push(event));
  for (const chunk of chunks) parser.feed(chunk);
  if (end) parser.end();
  return events;
}

describe("SSE parser", () => {
  it("parses event names and joins multi-line data with newlines", () => {
    expect(collect(['event: text_delta\ndata: {"a":1}\ndata: second line\n\n'])).toEqual([
      { event: "text_delta", data: '{"a":1}\nsecond line' },
    ]);
  });

  it("defaults the event name to message and ignores comment lines", () => {
    expect(collect([": keep-alive\ndata: hi\n\n"])).toEqual([{ event: "message", data: "hi" }]);
  });

  it("keeps one leading space after the colon only", () => {
    expect(collect(["data:  two spaces\n\n"])).toEqual([{ event: "message", data: " two spaces" }]);
  });

  it("handles lines split across chunks and CRLF endings", () => {
    expect(collect(["event: done", "\r\ndata: {}", "\r\n", "\r\n"])).toEqual([{ event: "done", data: "{}" }]);
  });

  it("emits several events from one chunk in order", () => {
    expect(collect(["event: a\ndata: 1\n\nevent: b\ndata: 2\n\n"])).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("discards an incomplete event at end of stream", () => {
    expect(collect(["event: x\ndata: y\n"])).toEqual([]);
  });

  it("reads a ReadableStream body and yields each event", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("event: a\ndata: 1\n\nevent: b\n"));
        controller.enqueue(encoder.encode("data: 2\n\n"));
        controller.close();
      },
    });
    const events: SSEEvent[] = [];
    for await (const event of readSSE(body)) events.push(event);
    expect(events).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });
});

describe("typed events", () => {
  it("parses chat events and ignores malformed or unknown ones", () => {
    expect(parseChatEvent({ event: "text_delta", data: '{"message_id":"m1","text":"Hi"}' })).toEqual({
      type: "text_delta",
      message_id: "m1",
      text: "Hi",
    });
    expect(parseChatEvent({ event: "done", data: "{}" })).toEqual({ type: "done" });
    expect(parseChatEvent({ event: "text_delta", data: "not json" })).toBeNull();
    expect(parseChatEvent({ event: "unknown", data: "{}" })).toBeNull();
  });

  it("maps a live event name to its payload and rejects unknown names", () => {
    expect(parseLiveEvent("task.updated", '{"task_id":"t1","status":"running"}')).toEqual({
      type: "task.updated",
      data: { task_id: "t1", status: "running" },
    });
    expect(parseLiveEvent("made.up", "{}")).toBeNull();
  });

  it("collects assistant text from deltas and prefers the final message", () => {
    const events: ChatStreamEvent[] = [
      { type: "text_delta", message_id: "m1", text: "Hel" },
      { type: "text_delta", message_id: "m1", text: "lo" },
    ];
    expect(collectAssistantText(events)).toBe("Hello");
    const message = {
      id: "m1",
      conversation_id: "c1",
      role: "assistant",
      content: "Hello there.",
      status: "complete",
      provider_id: null,
      model: null,
      task_id: null,
      meta: {},
      created_at: "2026-10-19T00:00:00Z",
      edited_at: null,
    } as const;
    expect(collectAssistantText([...events, { type: "assistant_done", message }])).toBe("Hello there.");
  });
});
