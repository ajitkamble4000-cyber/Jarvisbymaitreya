/**
 * Agent: composes config, secret storage, pairing, the backend connection, command verification, the dispatcher and
 * the outbox. The Electron shell and the headless runner both use this class and nothing lower-level.
 */
import * as os from "node:os";
import type { Platform } from "../platform/platform.js";
import type { AgentStatus, AgentState, ConfigSummary, PairInput } from "../shared/ipc-contract.js";
import {
  PROTOCOL_VERSION,
  type AgentErrorCode,
  type AgentResult,
  type Hello,
  type Resources,
} from "../shared/protocol.js";
import {
  ConfigError,
  configFilePath,
  enabledCapabilities,
  loadConfig,
  resourcesForServer,
  saveConfig,
  type AgentConfig,
  type PairingRecord,
} from "./config.js";
import { verifyCancel, verifyCommand, type VerifyResult } from "./command-verifier.js";
import { AgentConnection, type ConnectionEvent } from "./connection.js";
import { Dispatcher } from "./dispatcher.js";
import { silentLogger, type Logger } from "./logger.js";
import { Outbox } from "./outbox.js";
import { PairingError, pairWithServer } from "./pairing.js";
import { ReplayStore, replayStoreFileIn } from "./replay-store.js";
import { ServerUrlError, normaliseServerUrl, serverHostForDisplay } from "./server-url.js";
import { DEVICE_SECRET_NAME, SecretStoreError, type SecretStore } from "./secret-store.js";
import { ensurePrivateDir } from "./private-files.js";

export const AGENT_VERSION = "1.0.0";
const DEFAULT_DEVICE_TYPE = "laptop";

export interface AgentOptions {
  dataDir: string;
  platform: Platform;
  secretStore: SecretStore;
  logger?: Logger;
  fetchImpl?: typeof fetch;
  backoff?: { baseMs?: number; maxMs?: number; random?: () => number };
  /** Epoch milliseconds. Used for signature window checks; tests inject a fixed clock. */
  clock?: () => number;
}

export class Agent {
  readonly dataDir: string;
  private readonly configFile: string;
  private readonly platform: Platform;
  private readonly secretStore: SecretStore;
  private readonly logger: Logger;
  private readonly fetchImpl: typeof fetch | undefined;
  private readonly backoff: AgentOptions["backoff"];
  private readonly clock: () => number;
  private readonly outbox: Outbox;
  private replayStorePromise: Promise<ReplayStore> | null = null;
  /** Inbound command and cancel frames are handled one at a time, in arrival order. */
  private inbound: Promise<void> = Promise.resolve();
  private readonly dispatcher: Dispatcher;
  private readonly listeners = new Set<(status: AgentStatus) => void>();

  private config: AgentConfig | null = null;
  private secret: string | null = null;
  private connection: AgentConnection | null = null;
  private status: AgentStatus;

  constructor(options: AgentOptions) {
    this.dataDir = options.dataDir;
    this.configFile = configFilePath(options.dataDir);
    this.platform = options.platform;
    this.secretStore = options.secretStore;
    this.logger = options.logger ?? silentLogger;
    this.fetchImpl = options.fetchImpl;
    this.backoff = options.backoff;
    this.clock = options.clock ?? (() => Date.now());
    this.outbox = new Outbox(Outbox.fileIn(options.dataDir), this.logger);
    this.dispatcher = new Dispatcher({
      platform: this.platform,
      onResult: (result) => this.deliverResult(result),
      logger: this.logger,
    });
    this.status = this.makeStatus("unpaired", null);
  }

  /** Reads agent.json, the replay store and the outbox, then sets the initial status. Never touches the network. */
  async init(): Promise<void> {
    // agent.json is read first: the replay store needs to know whether the device is paired (see replayStore).
    try {
      this.config = await loadConfig(this.configFile, this.logger);
    } catch (error) {
      await this.replayStore();
      this.setStatus("error", userMessageFor(error));
      return;
    }
    await this.replayStore();
    await this.outbox.load();
    await this.refreshPairedState();
  }

  /** Starts the backend connection when the device is paired. Safe to call more than once. */
  async start(): Promise<void> {
    if (this.connection?.isRunning === true) {
      return;
    }
    const config = this.config;
    if (config === null) {
      this.setStatus("error", "agent.json could not be read.");
      return;
    }
    if (config.pairing === null) {
      this.setStatus("unpaired", null);
      return;
    }
    if (!(await this.loadSecret())) {
      return;
    }
    const pairing = config.pairing;
    let connection: AgentConnection;
    try {
      connection = new AgentConnection({
        serverUrl: pairing.serverUrl,
        websocketPath: pairing.websocketPath,
        secret: this.secret ?? "",
        buildHello: () => this.buildHello(),
        outbox: this.outbox,
        runningCommandIds: () => this.dispatcher.runningCommandIds(),
        onCommandFrame: (text) => this.handleCommandFrame(text),
        onCancelFrame: (text) => this.handleCancelFrame(text),
        onEvent: (event) => this.onConnectionEvent(event),
        logger: this.logger,
        backoff: this.backoff,
      });
    } catch (error) {
      this.setStatus("error", userMessageFor(error));
      return;
    }
    this.connection = connection;
    connection.start();
  }

  /** Closes the connection. Results still running stay in the outbox and go out at the next connection. */
  async stop(): Promise<void> {
    const connection = this.connection;
    this.connection = null;
    this.secret = null;
    if (connection !== null) {
      await connection.stop();
    }
    if (this.status.state === "connecting" || this.status.state === "connected" || this.status.state === "offline") {
      this.setStatus(this.config?.pairing ? "paired" : "unpaired", null);
    }
  }

  isRunning(): boolean {
    return this.connection?.isRunning === true;
  }

  /**
   * Pairs with the server using a one-time code. Throws PairingError, ServerUrlError or SecretStoreError with a message
   * that is safe to show. The code is never logged or stored.
   */
  async pair(input: PairInput): Promise<AgentStatus> {
    const config = this.config;
    if (config === null) {
      throw new ConfigError("agent.json could not be read.");
    }
    if (!this.secretStore.isAvailable()) {
      throw new SecretStoreError("Secure storage is unavailable on this device. Pairing was not started.");
    }
    const origin = normaliseServerUrl(input.serverUrl).origin;
    const deviceName = displayDeviceName(input.deviceName, this.platform.simulated);
    const systemInfo = this.platform.systemInfo();
    const response = await pairWithServer({
      serverUrl: origin,
      code: input.code.trim(),
      deviceName,
      deviceType: DEFAULT_DEVICE_TYPE,
      os: this.osName(),
      osVersion: systemInfo.os_version,
      agentVersion: AGENT_VERSION,
      capabilities: enabledCapabilities(config),
      resources: resourcesForServer(config),
      fetchImpl: this.fetchImpl,
    });

    // The secret is stored before the config points at it, so a config never names a device with no secret.
    await this.stop();
    await this.secretStore.set(DEVICE_SECRET_NAME, response.device_secret);
    const pairing: PairingRecord = {
      deviceId: response.device_id,
      deviceName,
      serverUrl: origin,
      websocketPath: response.websocket_path,
      heartbeatIntervalSeconds: response.heartbeat_interval_seconds,
      protocolVersion: response.protocol_version,
      pairedAt: this.platform.now().toISOString(),
    };
    this.config = { ...config, pairing };
    await saveConfig(this.configFile, this.config);
    // A paired device must have a replay file (see replayStore). Create the empty store now, so the next start is not
    // mistaken for a lost file. A failure is logged; pairing itself still succeeds.
    await (await this.replayStore()).persist().catch(() => {
      this.logger.warn("The replay store could not be created when the device was paired.");
    });
    this.logger.info("Device paired.", { protocolVersion: PROTOCOL_VERSION });
    await this.start();
    return this.status;
  }

  /** Stops the connection and deletes the local secret. The device stays registered on the server until revoked there. */
  async unpair(): Promise<AgentStatus> {
    await this.stop();
    if (this.secretStore.isAvailable()) {
      await this.secretStore.delete(DEVICE_SECRET_NAME);
    }
    if (this.config !== null) {
      this.config = { ...this.config, pairing: null };
      await saveConfig(this.configFile, this.config);
    }
    this.setStatus("unpaired", null);
    return this.status;
  }

  /** Ensures the config folder exists, so the UI can open it. */
  async ensureDataDir(): Promise<void> {
    await ensurePrivateDir(this.dataDir);
  }

  getStatus(): AgentStatus {
    return { ...this.status };
  }

  onStatusChange(listener: (status: AgentStatus) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Only ids, labels, capabilities and the server host. No paths and no secrets. */
  getConfigSummary(): ConfigSummary {
    const config = this.config;
    return {
      paired: config?.pairing != null,
      serverHost: config?.pairing ? serverHostForDisplay(config.pairing.serverUrl) : null,
      deviceName: config?.pairing?.deviceName ?? null,
      capabilities: config ? enabledCapabilities(config) : [],
      apps: config?.approvedApps.map((entry) => ({ id: entry.id, label: entry.label })) ?? [],
      urls: config?.approvedUrls.map((entry) => ({ id: entry.id, label: entry.label })) ?? [],
      folders: config?.approvedFolders.map((entry) => ({ id: entry.id, label: entry.label })) ?? [],
      projects: config?.approvedProjects.map((entry) => ({ id: entry.id, label: entry.label })) ?? [],
      files: config?.approvedFiles.map((entry) => ({ id: entry.id, label: entry.label })) ?? [],
    };
  }

  // ---- inbound frames ---------------------------------------------------------------------

  private handleCommandFrame(text: string): void {
    this.enqueueInbound(() => this.processCommandFrame(text));
  }

  private async processCommandFrame(text: string): Promise<void> {
    const config = this.config;
    const secret = this.secret;
    if (config === null || secret === null) {
      this.logger.warn("Dropped a command received while no secret is loaded.");
      return;
    }
    const store = await this.replayStore();
    const before = store.cache.revision;
    let verdict: VerifyResult = verifyCommand(text, { secret, config, nowMs: this.clock(), replay: store.cache });
    // The replay record is on disk before the command can run. If it cannot be written, the command is refused.
    if (store.cache.revision !== before && !(await this.recordReplay(store)) && verdict.ok) {
      verdict = {
        ok: false,
        code: "agent_error",
        message: "The command could not be recorded, so it was not run.",
        commandId: verdict.command.id,
      };
    }
    if (verdict.ok) {
      if (!this.dispatcher.submit(verdict.command)) {
        this.logger.warn("Ignored a command whose id is already running.");
      }
      return;
    }
    await this.rejectCommand(verdict).catch(() => {
      this.logger.error("Could not record a rejected command.");
    });
  }

  /** Writes the replay store. Returns false, and logs, when the write fails. */
  private async recordReplay(store: ReplayStore): Promise<boolean> {
    try {
      await store.persist();
      return true;
    } catch {
      this.logger.error("The replay store could not be written.");
      return false;
    }
  }

  private async rejectCommand(rejection: Extract<VerifyResult, { ok: false }>): Promise<void> {
    const { commandId, code } = rejection;
    this.logger.info("Rejected a command.", { code, hasId: commandId !== null });
    if (commandId === null) {
      return;
    }
    if (code === "replayed") {
      // A replay must never produce a new result for a command the backend already knows. Resend what we hold, if anything.
      const existing = this.outbox.get(commandId);
      if (existing !== undefined && this.connection !== null) {
        await this.connection.sendResult(existing);
      }
      return;
    }
    const result: AgentResult = {
      type: "result",
      command_id: commandId,
      status: "failed",
      output: {},
      error: { code: code as AgentErrorCode, message: rejection.message, details: {} },
      started_at: null,
      finished_at: this.platform.now().toISOString(),
    };
    await this.deliverResult(result);
  }

  private handleCancelFrame(text: string): void {
    this.enqueueInbound(() => this.processCancelFrame(text));
  }

  private async processCancelFrame(text: string): Promise<void> {
    const config = this.config;
    const secret = this.secret;
    if (config === null || secret === null) {
      return;
    }
    const store = await this.replayStore();
    const before = store.cache.revision;
    const verified = verifyCancel(text, { secret, config, nowMs: this.clock(), replay: store.cache });
    if (store.cache.revision !== before) {
      // A failed write does not drop the cancel: the replay check above already used the in-memory record.
      await this.recordReplay(store);
    }
    if (!verified.ok) {
      this.logger.warn("Ignored an invalid cancel.", { reason: verified.reason });
      return;
    }
    const outcome = this.dispatcher.cancel(verified.commandId);
    this.logger.info("Cancel received.", { cancelled: outcome.cancelled, reason: outcome.cancelled ? "" : outcome.reason });
  }

  /**
   * Runs one inbound frame after the earlier ones. A cancel therefore waits until the command it names has been
   * recorded and submitted, so it can never overtake that command while the replay record is being written.
   */
  private enqueueInbound(task: () => Promise<void>): void {
    this.inbound = this.inbound.then(task).catch(() => {
      this.logger.error("Could not handle an inbound frame.");
    });
  }

  /**
   * Opens the replay store once. Opening never fails for a bad file: it comes back empty or degraded. It is opened at
   * start-up, before any command arrives, because the degraded floor is the start-up time. A paired device must have
   * a replay file; without one it is degraded.
   */
  private replayStore(): Promise<ReplayStore> {
    if (this.replayStorePromise === null) {
      this.replayStorePromise = ReplayStore.open({
        file: replayStoreFileIn(this.dataDir),
        logger: this.logger,
        clock: this.clock,
        expectExisting: this.config?.pairing != null,
      });
    }
    return this.replayStorePromise;
  }

  // ---- outbound -----------------------------------------------------------------------------

  /** Results always go to the outbox first. They are sent at once when the connection is up. */
  private async deliverResult(result: AgentResult): Promise<void> {
    if (this.connection !== null) {
      await this.connection.sendResult(result);
      return;
    }
    await this.outbox.add(result);
  }

  private buildHello(): Hello {
    const config = this.config;
    const resources: Resources = config
      ? resourcesForServer(config)
      : { apps: [], urls: [], folders: [], projects: [], files: [], checks: [] };
    return {
      type: "hello",
      agent_version: AGENT_VERSION,
      os: this.osName(),
      os_version: this.platform.systemInfo().os_version,
      capabilities: config ? enabledCapabilities(config) : [],
      resources,
    };
  }

  // ---- connection events ----------------------------------------------------------------------

  private onConnectionEvent(event: ConnectionEvent): void {
    switch (event.kind) {
      case "connecting":
        this.setStatus("connecting", null);
        return;
      case "connected":
        this.setStatus("connected", null);
        return;
      case "offline":
        this.setStatus("offline", `Reconnecting in ${Math.max(1, Math.round(event.retryInMs / 1000))} s.`);
        return;
      case "re-pair-required":
        this.connection = null;
        this.secret = null;
        this.setStatus("re-pair-required", "The server rejected this device. Pair this PC again.");
        return;
      case "revoked":
        this.connection = null;
        this.secret = null;
        this.setStatus("unpaired", "This device was revoked on the server. Pair it again to reconnect.");
        this.clearPairingAfterRevocation().catch(() => {
          this.logger.error("Could not clear the revoked pairing.");
        });
        return;
      case "replaced":
        this.connection = null;
        this.secret = null;
        this.setStatus("error", "Another agent connection replaced this one.");
        return;
    }
  }

  private async clearPairingAfterRevocation(): Promise<void> {
    if (this.secretStore.isAvailable()) {
      await this.secretStore.delete(DEVICE_SECRET_NAME);
    }
    if (this.config !== null) {
      this.config = { ...this.config, pairing: null };
      await saveConfig(this.configFile, this.config);
    }
  }

  // ---- status ---------------------------------------------------------------------------------

  private async refreshPairedState(): Promise<void> {
    const config = this.config;
    if (config === null) {
      this.setStatus("error", "agent.json could not be read.");
      return;
    }
    if (config.pairing === null) {
      this.setStatus("unpaired", null);
      return;
    }
    if (!this.secretStore.isAvailable()) {
      this.setStatus("error", "Secure storage is unavailable on this device.");
      return;
    }
    try {
      const secret = await this.secretStore.get(DEVICE_SECRET_NAME);
      if (secret === null) {
        this.setStatus("re-pair-required", "The device secret is missing. Pair this PC again.");
        return;
      }
      this.setStatus("paired", null);
    } catch (error) {
      this.setStatus("error", userMessageFor(error));
    }
  }

  /** Loads the secret into memory for verification. Returns false, with status set, when it cannot be loaded. */
  private async loadSecret(): Promise<boolean> {
    try {
      const secret = await this.secretStore.get(DEVICE_SECRET_NAME);
      if (secret === null) {
        this.setStatus("re-pair-required", "The device secret is missing. Pair this PC again.");
        return false;
      }
      this.secret = secret;
      return true;
    } catch (error) {
      this.setStatus("error", userMessageFor(error));
      return false;
    }
  }

  private osName(): string {
    return this.platform.kind === "windows" ? "windows" : "simulated";
  }

  private setStatus(state: AgentState, message: string | null): void {
    this.status = this.makeStatus(state, message);
    for (const listener of this.listeners) {
      try {
        listener({ ...this.status });
      } catch {
        this.logger.error("A status listener failed.");
      }
    }
  }

  private makeStatus(state: AgentState, message: string | null): AgentStatus {
    const pairing = this.config?.pairing ?? null;
    return {
      state,
      message,
      simulated: this.platform.simulated,
      banner: this.platform.banner,
      deviceName: pairing?.deviceName ?? null,
      serverHost: pairing ? serverHostForDisplay(pairing.serverUrl) : null,
    };
  }
}

/** Short text for the UI. Known errors carry messages written to be safe; anything else gets a generic line. */
export function userMessageFor(error: unknown): string {
  if (
    error instanceof PairingError ||
    error instanceof ServerUrlError ||
    error instanceof SecretStoreError ||
    error instanceof ConfigError
  ) {
    return error.message;
  }
  return "The operation failed. Check the agent log for details.";
}

function displayDeviceName(requested: string | undefined, simulated: boolean): string {
  const trimmed = requested?.trim() ?? "";
  if (trimmed.length > 0) {
    return trimmed.slice(0, 120);
  }
  const host = os.hostname() || "PC";
  return (simulated ? `${host} (simulated)` : host).slice(0, 120);
}
