/**
 * Outbox: command results are written to outbox.json before they are sent, and stay there until the backend has
 * accepted them (a heartbeat or a reconnection completes without a send error). Entries are unique by command id.
 */
import * as path from "node:path";
import { ResultSchema, type AgentResult } from "../shared/protocol.js";
import { readTextIfExists, writePrivateFileAtomic } from "./private-files.js";
import type { Logger } from "./logger.js";

export const OUTBOX_FILE_NAME = "outbox.json";
const OUTBOX_VERSION = 1;

interface OutboxFile {
  version: number;
  entries: AgentResult[];
}

export class Outbox {
  private readonly file: string;
  private readonly logger: Logger | undefined;
  private entries = new Map<string, AgentResult>();
  private loaded = false;
  private writeChain: Promise<void> = Promise.resolve();

  constructor(file: string, logger?: Logger) {
    this.file = file;
    this.logger = logger;
  }

  static fileIn(dataDir: string): string {
    return path.join(dataDir, OUTBOX_FILE_NAME);
  }

  /** Reads the outbox from disk. Invalid entries are dropped; an unreadable file starts an empty outbox. */
  async load(): Promise<void> {
    if (this.loaded) {
      return;
    }
    this.loaded = true;
    const text = await readTextIfExists(this.file);
    if (text === null) {
      return;
    }
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch {
      this.logger?.warn("outbox.json is not valid JSON; starting with an empty outbox.");
      return;
    }
    const rawEntries = (value as Partial<OutboxFile> | null)?.entries;
    if (!Array.isArray(rawEntries)) {
      this.logger?.warn("outbox.json has an unexpected shape; starting with an empty outbox.");
      return;
    }
    for (const raw of rawEntries) {
      const parsed = ResultSchema.safeParse(raw);
      if (parsed.success) {
        this.entries.set(parsed.data.command_id, parsed.data);
      } else {
        this.logger?.warn("Dropped an invalid outbox entry.");
      }
    }
  }

  pending(): AgentResult[] {
    return [...this.entries.values()];
  }

  has(commandId: string): boolean {
    return this.entries.has(commandId);
  }

  get(commandId: string): AgentResult | undefined {
    return this.entries.get(commandId);
  }

  size(): number {
    return this.entries.size;
  }

  /** Persists the result before returning. A second result for the same command id replaces the first. */
  async add(result: AgentResult): Promise<void> {
    this.entries.set(result.command_id, result);
    await this.persist();
  }

  /** Removes entries the backend has accepted. Unknown ids are ignored. */
  async acknowledge(commandIds: readonly string[]): Promise<void> {
    let changed = false;
    for (const id of commandIds) {
      if (this.entries.delete(id)) {
        changed = true;
      }
    }
    if (changed) {
      await this.persist();
    }
  }

  private persist(): Promise<void> {
    // Writes run one after another so that the file always reflects the latest state.
    const snapshot: OutboxFile = { version: OUTBOX_VERSION, entries: [...this.entries.values()] };
    const text = `${JSON.stringify(snapshot, null, 2)}\n`;
    // A failed write must not block later writes, so the chain continues past rejections.
    const next = this.writeChain.catch(() => undefined).then(() => writePrivateFileAtomic(this.file, text));
    this.writeChain = next;
    return next;
  }
}
