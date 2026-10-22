/**
 * Channel names and payload types shared by the main process, the preload script and the renderer.
 * This file has no runtime dependencies so that the sandboxed preload bundle stays small.
 */

export const IPC = {
  getStatus: "agent:get-status",
  pair: "agent:pair",
  unpair: "agent:unpair",
  getConfigSummary: "agent:get-config-summary",
  openConfigFolder: "agent:open-config-folder",
  statusChanged: "agent:status-changed",
  // Sent by the quick-ask popup's preload, never by the agent window.
  quickHide: "quick:hide",
} as const;

export type AgentState =
  | "connecting"
  | "connected"
  | "offline"
  | "paired"
  | "unpaired"
  | "re-pair-required"
  | "error";

export interface AgentStatus {
  state: AgentState;
  /** Short, human-readable detail. Never contains a secret, a pairing code or a local path. */
  message: string | null;
  simulated: boolean;
  /** Shown as a banner in the UI when the platform is simulated. */
  banner: string | null;
  deviceName: string | null;
  serverHost: string | null;
}

export interface PairInput {
  serverUrl: string;
  code: string;
  deviceName?: string;
}

export type PairResult = { ok: true; status: AgentStatus } | { ok: false; message: string };

export interface ResourceView {
  id: string;
  label: string;
}

export interface ConfigSummary {
  paired: boolean;
  serverHost: string | null;
  deviceName: string | null;
  capabilities: string[];
  apps: ResourceView[];
  urls: ResourceView[];
  folders: ResourceView[];
  projects: ResourceView[];
  files: ResourceView[];
}

/** The typed API the preload script exposes as window.jarvis. Nothing else reaches the renderer. */
export interface JarvisApi {
  getStatus(): Promise<AgentStatus>;
  pair(input: PairInput): Promise<PairResult>;
  unpair(): Promise<AgentStatus>;
  getConfigSummary(): Promise<ConfigSummary>;
  openConfigFolder(): Promise<void>;
  onStatus(listener: (status: AgentStatus) => void): () => void;
}

declare global {
  interface Window {
    jarvis: JarvisApi;
  }
}
