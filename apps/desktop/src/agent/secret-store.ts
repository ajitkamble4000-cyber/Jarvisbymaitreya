/**
 * Device secret storage. The interface has two implementations:
 * - SafeStorageSecretStore encrypts with Electron safeStorage (DPAPI on Windows). It is injected, so this module
 *   has no Electron import and is testable on Linux.
 * - FileSecretStore keeps values in an owner-only JSON file. It is for the headless runner and tests only.
 * Secret values are never logged and never returned to the renderer.
 */
import { writePrivateFileAtomic, readTextIfExists } from "./private-files.js";

export const DEVICE_SECRET_NAME = "device_secret";

export interface SecretStore {
  readonly kind: "safeStorage" | "file" | "memory";
  /** False when the platform cannot protect secrets. Pairing is refused in that case. */
  isAvailable(): boolean;
  get(name: string): Promise<string | null>;
  set(name: string, value: string): Promise<void>;
  delete(name: string): Promise<void>;
}

export class SecretStoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SecretStoreError";
  }
}

/** The subset of Electron's safeStorage used here. */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plainText: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

type EncryptedMap = Record<string, string>;

function parseMap(text: string | null): Record<string, string> {
  if (text === null) {
    return {};
  }
  const value: unknown = JSON.parse(text);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new SecretStoreError("The secret store file is damaged.");
  }
  const result: Record<string, string> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "string") {
      result[key] = entry;
    }
  }
  return result;
}

export class SafeStorageSecretStore implements SecretStore {
  readonly kind = "safeStorage" as const;
  private readonly storage: SafeStorageLike;
  private readonly file: string;

  constructor(storage: SafeStorageLike, file: string) {
    this.storage = storage;
    this.file = file;
  }

  isAvailable(): boolean {
    try {
      return this.storage.isEncryptionAvailable();
    } catch {
      return false;
    }
  }

  async get(name: string): Promise<string | null> {
    const map = parseMap(await readTextIfExists(this.file)) as EncryptedMap;
    const encoded = map[name];
    if (encoded === undefined) {
      return null;
    }
    if (!this.isAvailable()) {
      throw new SecretStoreError("Secure storage is unavailable on this device.");
    }
    return this.storage.decryptString(Buffer.from(encoded, "base64"));
  }

  async set(name: string, value: string): Promise<void> {
    if (!this.isAvailable()) {
      throw new SecretStoreError("Secure storage is unavailable on this device.");
    }
    const map = parseMap(await readTextIfExists(this.file)) as EncryptedMap;
    map[name] = this.storage.encryptString(value).toString("base64");
    await writePrivateFileAtomic(this.file, `${JSON.stringify(map)}\n`);
  }

  async delete(name: string): Promise<void> {
    const map = parseMap(await readTextIfExists(this.file)) as EncryptedMap;
    if (!(name in map)) {
      return;
    }
    delete map[name];
    await writePrivateFileAtomic(this.file, `${JSON.stringify(map)}\n`);
  }
}

/** Plain values in an owner-only file. Headless and test use only. */
export class FileSecretStore implements SecretStore {
  readonly kind = "file" as const;
  private readonly file: string;

  constructor(file: string) {
    this.file = file;
  }

  isAvailable(): boolean {
    return true;
  }

  async get(name: string): Promise<string | null> {
    const map = parseMap(await readTextIfExists(this.file));
    return map[name] ?? null;
  }

  async set(name: string, value: string): Promise<void> {
    const map = parseMap(await readTextIfExists(this.file));
    map[name] = value;
    await writePrivateFileAtomic(this.file, `${JSON.stringify(map)}\n`);
  }

  async delete(name: string): Promise<void> {
    const map = parseMap(await readTextIfExists(this.file));
    if (!(name in map)) {
      return;
    }
    delete map[name];
    await writePrivateFileAtomic(this.file, `${JSON.stringify(map)}\n`);
  }
}

/** In-process store for unit tests. */
export class MemorySecretStore implements SecretStore {
  readonly kind = "memory" as const;
  private readonly values = new Map<string, string>();
  private readonly available: boolean;

  constructor(options: { available?: boolean } = {}) {
    this.available = options.available ?? true;
  }

  isAvailable(): boolean {
    return this.available;
  }

  async get(name: string): Promise<string | null> {
    return this.values.get(name) ?? null;
  }

  async set(name: string, value: string): Promise<void> {
    if (!this.available) {
      throw new SecretStoreError("Secure storage is unavailable on this device.");
    }
    this.values.set(name, value);
  }

  async delete(name: string): Promise<void> {
    this.values.delete(name);
  }
}
