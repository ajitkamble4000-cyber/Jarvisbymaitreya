/**
 * Replay protection that survives restarts. Every command or cancel that reaches step 4 of the verifier is recorded as
 * {id, nonce, seen_at} in replay-store.json, in the agent's data directory, before a command can run.
 *
 * - ReplayCache is the in-memory ledger the verifier reads and writes. It keeps a record for REPLAY_WINDOW_MS.
 * - ReplayStore loads that ledger from the file on start and writes it back atomically (temporary file, fsync, rename).
 * - A missing file means a first start, so the store starts empty. The exception is a paired device (expectExisting):
 *   it has run commands, so a missing file is treated like a corrupt one.
 * - A corrupt, unreadable or missing-when-expected file means the agent does not know what it ran before, so the store
 *   is degraded: commands issued before start-up are refused. That floor is written into the file (issued_floor_ms), so
 *   it survives restarts and lasts one replay window.
 */
import * as path from "node:path";
import { z } from "zod";
import { silentLogger, type Logger } from "./logger.js";
import { readTextIfExists, writePrivateFileAtomic } from "./private-files.js";

/**
 * How long a command id or nonce is remembered. A command lives at most MAX_COMMAND_LIFETIME_MS (540 s) and is accepted
 * at most CLOCK_SKEW_MS (30 s) early, so 10 minutes always outlives it.
 */
export const REPLAY_WINDOW_MS = 10 * 60 * 1000;
export const REPLAY_STORE_MAX_ENTRIES = 10_000;
export const REPLAY_STORE_FILE_NAME = "replay-store.json";
const STORE_VERSION = 1;

export interface ReplayRecord {
  id: string;
  nonce: string;
  seen_at: number;
}

const RecordSchema = z
  .object({
    id: z.string().min(1).max(128),
    nonce: z.string().min(1).max(256),
    seen_at: z.number().int().nonnegative(),
  })
  .strict();

const StoreFileSchema = z
  .object({
    version: z.literal(STORE_VERSION),
    entries: z.array(RecordSchema),
    // Set only while the store is (or was recently) degraded. See ReplayCache.issuedFloorAt.
    issued_floor_ms: z.number().int().nonnegative().optional(),
  })
  .strict();

export function replayStoreFileIn(dataDir: string): string {
  return path.join(dataDir, REPLAY_STORE_FILE_NAME);
}

export interface ReplayCacheOptions {
  windowMs?: number;
  limit?: number;
  /** Commands issued before this instant are refused. Null, the default, checks every command against the records. */
  issuedFloorMs?: number | null;
  /** Called each time the limit forces the oldest records out. */
  onLimitReached?: () => void;
}

/** In-memory ledger of command ids and nonces. Synchronous, so the verifier can use it directly. */
export class ReplayCache {
  private readonly windowMs: number;
  private readonly limit: number;
  private readonly onLimitReached: (() => void) | undefined;
  private floorMs: number | null;
  private order: ReplayRecord[] = [];
  private readonly byId = new Map<string, ReplayRecord>();
  private readonly byNonce = new Map<string, ReplayRecord>();
  private changes = 0;

  constructor(options: ReplayCacheOptions = {}) {
    this.windowMs = options.windowMs ?? REPLAY_WINDOW_MS;
    this.limit = options.limit ?? REPLAY_STORE_MAX_ENTRIES;
    this.floorMs = options.issuedFloorMs ?? null;
    this.onLimitReached = options.onLimitReached;
  }

  /** The stored floor, whether or not it still applies. See issuedFloorAt. */
  get issuedFloorMs(): number | null {
    return this.floorMs;
  }

  /**
   * Commands issued before the returned instant are refused, or null when no floor applies at ``nowMs``. The floor ends
   * one replay window after it was set: a command lives at most MAX_COMMAND_LIFETIME_MS (540 s), so by then every command
   * issued before the floor has expired.
   */
  issuedFloorAt(nowMs: number): number | null {
    if (this.floorMs === null || nowMs >= this.floorMs + this.windowMs) {
      return null;
    }
    return this.floorMs;
  }

  /** Counts the records made by remember(). A caller compares it before and after to skip a needless write. */
  get revision(): number {
    return this.changes;
  }

  hasId(id: string, nowMs: number): boolean {
    return this.live(this.byId.get(id), nowMs);
  }

  hasNonce(nonce: string, nowMs: number): boolean {
    return this.live(this.byNonce.get(nonce), nowMs);
  }

  remember(record: { id: string; nonce: string }, nowMs: number): void {
    this.prune(nowMs);
    this.insert({ id: record.id, nonce: record.nonce, seen_at: nowMs });
    this.changes += 1;
    this.enforceLimit();
  }

  /** Loads records read from the store file. Expired records are dropped, and the oldest go beyond the limit. */
  restore(records: readonly ReplayRecord[], nowMs: number): void {
    const ordered = [...records].sort((a, b) => a.seen_at - b.seen_at);
    for (const record of ordered) {
      if (record.seen_at + this.windowMs > nowMs) {
        this.insert({ id: record.id, nonce: record.nonce, seen_at: record.seen_at });
      }
    }
    this.enforceLimit();
  }

  /** The live records, oldest first. Expired records are dropped first. */
  snapshot(nowMs: number): ReplayRecord[] {
    this.prune(nowMs);
    return this.order.map((entry) => ({ id: entry.id, nonce: entry.nonce, seen_at: entry.seen_at }));
  }

  size(): number {
    return this.order.length;
  }

  private live(entry: ReplayRecord | undefined, nowMs: number): boolean {
    return entry !== undefined && entry.seen_at + this.windowMs > nowMs;
  }

  private insert(entry: ReplayRecord): void {
    this.order.push(entry);
    this.byId.set(entry.id, entry);
    this.byNonce.set(entry.nonce, entry);
  }

  private remove(entry: ReplayRecord): void {
    // An index entry may already point at a newer record with the same key; only drop it when it is this record.
    if (this.byId.get(entry.id) === entry) {
      this.byId.delete(entry.id);
    }
    if (this.byNonce.get(entry.nonce) === entry) {
      this.byNonce.delete(entry.nonce);
    }
  }

  private prune(nowMs: number): void {
    const alive: ReplayRecord[] = [];
    for (const entry of this.order) {
      if (entry.seen_at + this.windowMs > nowMs) {
        alive.push(entry);
      } else {
        this.remove(entry);
      }
    }
    this.order = alive;
  }

  private enforceLimit(): void {
    const excess = this.order.length - this.limit;
    if (excess <= 0) {
      return;
    }
    for (const entry of this.order.splice(0, excess)) {
      this.remove(entry);
    }
    this.onLimitReached?.();
  }
}

export class ReplayStoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReplayStoreError";
  }
}

export interface ReplayStoreOptions {
  file: string;
  logger?: Logger;
  /** Epoch milliseconds. Tests inject a fixed clock. */
  clock?: () => number;
  /** The entry limit. Defaults to REPLAY_STORE_MAX_ENTRIES. */
  maxEntries?: number;
  /** True for a paired device, which has run commands. A missing file is then degraded, not a first start. */
  expectExisting?: boolean;
}

interface StoredState {
  records: ReplayRecord[];
  issuedFloorMs: number | null;
}

/** Reads the file text into records and the stored floor. A missing file is an empty store. Any other problem throws. */
function parseStoreText(text: string | null): StoredState {
  if (text === null) {
    return { records: [], issuedFloorMs: null };
  }
  const parsed = StoreFileSchema.safeParse(JSON.parse(text));
  if (!parsed.success) {
    throw new ReplayStoreError("The replay store has an unexpected shape.");
  }
  return { records: parsed.data.entries, issuedFloorMs: parsed.data.issued_floor_ms ?? null };
}

export class ReplayStore {
  readonly cache: ReplayCache;
  private readonly file: string;
  private readonly logger: Logger;
  private readonly clock: () => number;
  private degradedNow: boolean;
  private writes: Promise<void> = Promise.resolve();

  private constructor(options: {
    file: string;
    logger: Logger;
    clock: () => number;
    cache: ReplayCache;
    degraded: boolean;
  }) {
    this.file = options.file;
    this.logger = options.logger;
    this.clock = options.clock;
    this.cache = options.cache;
    this.degradedNow = options.degraded;
  }

  /**
   * Opens the store and loads it. Never throws for a bad file: a corrupt or unreadable file gives a degraded store,
   * and a missing file gives an empty one. Opening never writes.
   */
  static async open(options: ReplayStoreOptions): Promise<ReplayStore> {
    const clock = options.clock ?? (() => Date.now());
    const logger = options.logger ?? silentLogger;
    const limit = options.maxEntries ?? REPLAY_STORE_MAX_ENTRIES;
    const startedAtMs = clock();
    const onLimitReached = (): void => {
      logger.warn("Replay store reached its entry limit; the oldest entries were dropped.", { limit });
    };

    const degraded = (reason: string): ReplayStore => {
      logger.warn(
        `Replay store is ${reason}. Replay protection is degraded until the file is recreated; commands issued before start-up are refused.`,
      );
      // Every command issued before start-up is refused. Commands from earlier degraded periods are older still.
      const cache = new ReplayCache({ limit, issuedFloorMs: startedAtMs, onLimitReached });
      return new ReplayStore({ file: options.file, logger, clock, cache, degraded: true });
    };

    let text: string | null;
    let stored: StoredState;
    try {
      text = await readTextIfExists(options.file);
      stored = parseStoreText(text);
    } catch {
      return degraded("corrupt or unreadable");
    }
    if (text === null && options.expectExisting === true) {
      return degraded("missing although this device is paired");
    }

    const cache = new ReplayCache({ limit, issuedFloorMs: stored.issuedFloorMs, onLimitReached });
    cache.restore(stored.records, startedAtMs);
    return new ReplayStore({ file: options.file, logger, clock, cache, degraded: false });
  }

  /** True while the file is corrupt or unreadable and has not yet been recreated. */
  get degraded(): boolean {
    return this.degradedNow;
  }

  /**
   * Writes the live records to the file, atomically. Writes run one after another. Expired records are dropped first.
   * Throws ReplayStoreError when the write fails. A successful write recreates the file and ends degraded mode.
   */
  persist(): Promise<void> {
    const write = this.writes.catch(() => undefined).then(() => this.write());
    this.writes = write;
    return write;
  }

  private async write(): Promise<void> {
    const entries = this.cache.snapshot(this.clock());
    // The floor is kept in the file. Dropping it on a write would let commands issued before start-up run again.
    const document: { version: number; entries: ReplayRecord[]; issued_floor_ms?: number } = {
      version: STORE_VERSION,
      entries,
    };
    if (this.cache.issuedFloorMs !== null) {
      document.issued_floor_ms = this.cache.issuedFloorMs;
    }
    const text = `${JSON.stringify(document)}\n`;
    try {
      await writePrivateFileAtomic(this.file, text);
    } catch {
      throw new ReplayStoreError("The replay store could not be written.");
    }
    if (this.degradedNow) {
      this.degradedNow = false;
      this.logger.info("Replay store recreated; replay protection is no longer degraded.");
    }
  }
}
