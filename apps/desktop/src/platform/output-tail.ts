/**
 * Keeps the last maxBytes of a byte stream, so a check that prints a lot cannot grow the agent's memory or the result.
 * Output is only ever stored and sent as text. It is never parsed or interpreted.
 */
export class OutputTail {
  private buffer: Buffer = Buffer.alloc(0);
  private dropped = false;

  constructor(private readonly maxBytes: number) {
    if (!Number.isInteger(maxBytes) || maxBytes < 1) {
      throw new RangeError("maxBytes must be a positive integer");
    }
  }

  push(chunk: Buffer): void {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    if (this.buffer.length > this.maxBytes) {
      this.dropped = true;
      this.buffer = this.buffer.subarray(this.buffer.length - this.maxBytes);
    }
  }

  get truncated(): boolean {
    return this.dropped;
  }

  text(): string {
    let start = 0;
    if (this.dropped) {
      // Skip the continuation bytes of a character whose first byte was dropped.
      while (start < this.buffer.length && ((this.buffer[start] ?? 0) & 0xc0) === 0x80) {
        start += 1;
      }
    }
    return this.buffer.subarray(start).toString("utf8");
  }
}

/** The tail of a text, at most maxBytes long in UTF-8, for scripted or simulated output. */
export function tailOfText(text: string, maxBytes: number): { output: string; truncated: boolean } {
  const tail = new OutputTail(maxBytes);
  tail.push(Buffer.from(text, "utf8"));
  return { output: tail.text(), truncated: tail.truncated };
}
