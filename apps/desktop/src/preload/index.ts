/**
 * Preload script (sandboxed). It exposes one typed object, window.jarvis, built from fixed channel names.
 * It never exposes ipcRenderer, the channel names are not caller-supplied, and nothing else crosses the bridge.
 * This file is bundled into a single script by scripts/build-bundles.mjs, because a sandboxed preload cannot
 * require local modules.
 */
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";
import {
  IPC,
  type AgentStatus,
  type ConfigSummary,
  type JarvisApi,
  type PairInput,
  type PairResult,
} from "../shared/ipc-contract.js";

const api: JarvisApi = {
  getStatus: (): Promise<AgentStatus> => ipcRenderer.invoke(IPC.getStatus) as Promise<AgentStatus>,
  pair: (input: PairInput): Promise<PairResult> => ipcRenderer.invoke(IPC.pair, input) as Promise<PairResult>,
  unpair: (): Promise<AgentStatus> => ipcRenderer.invoke(IPC.unpair) as Promise<AgentStatus>,
  getConfigSummary: (): Promise<ConfigSummary> =>
    ipcRenderer.invoke(IPC.getConfigSummary) as Promise<ConfigSummary>,
  openConfigFolder: (): Promise<void> => ipcRenderer.invoke(IPC.openConfigFolder) as Promise<void>,
  onStatus: (listener: (status: AgentStatus) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, status: AgentStatus): void => listener(status);
    ipcRenderer.on(IPC.statusChanged, handler);
    return () => {
      ipcRenderer.removeListener(IPC.statusChanged, handler);
    };
  },
};

contextBridge.exposeInMainWorld("jarvis", api);
