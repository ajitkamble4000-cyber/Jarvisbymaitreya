/**
 * IPC handlers for the main process. Every request is checked for its sender (the renderer's own file) and every
 * payload is validated with zod before use. Errors reach the renderer as short, safe messages.
 */
import { BrowserWindow, ipcMain, type IpcMainEvent, type IpcMainInvokeEvent } from "electron";
import { userMessageFor, type Agent } from "../agent/agent.js";
import { IPC, type AgentStatus, type ConfigSummary, type PairResult } from "../shared/ipc-contract.js";
import { PairInputSchema } from "../shared/ipc-schemas.js";
import { isAllowedUrl } from "./window.js";

export interface IpcDependencies {
  agent: Agent;
  allowedUrl: string;
  openConfigFolder: () => Promise<void>;
}

function assertTrustedSender(event: IpcMainInvokeEvent | IpcMainEvent, allowedUrl: string): void {
  const url = "senderFrame" in event && event.senderFrame !== null ? event.senderFrame.url : "";
  if (!isAllowedUrl(url, allowedUrl)) {
    throw new Error("Request refused: the sender is not the agent window.");
  }
}

function assertNoPayload(args: readonly unknown[]): void {
  if (args.length > 0 && args[0] !== undefined) {
    throw new Error("Invalid request.");
  }
}

/** Registers every channel. Returns a function that removes them again (used by tests and on quit). */
export function registerIpcHandlers(deps: IpcDependencies): () => void {
  const { agent, allowedUrl } = deps;

  ipcMain.handle(IPC.getStatus, (event: IpcMainInvokeEvent, ...args: unknown[]): AgentStatus => {
    assertTrustedSender(event, allowedUrl);
    assertNoPayload(args);
    return agent.getStatus();
  });

  ipcMain.handle(IPC.pair, async (event: IpcMainInvokeEvent, payload: unknown): Promise<PairResult> => {
    assertTrustedSender(event, allowedUrl);
    const parsed = PairInputSchema.safeParse(payload);
    if (!parsed.success) {
      return { ok: false, message: "The pairing details are not valid." };
    }
    try {
      const status = await agent.pair({
        serverUrl: parsed.data.serverUrl,
        code: parsed.data.code,
        deviceName: parsed.data.deviceName,
      });
      return { ok: true, status };
    } catch (error) {
      return { ok: false, message: userMessageFor(error) };
    }
  });

  ipcMain.handle(IPC.unpair, async (event: IpcMainInvokeEvent, ...args: unknown[]): Promise<AgentStatus> => {
    assertTrustedSender(event, allowedUrl);
    assertNoPayload(args);
    return agent.unpair();
  });

  ipcMain.handle(IPC.getConfigSummary, (event: IpcMainInvokeEvent, ...args: unknown[]): ConfigSummary => {
    assertTrustedSender(event, allowedUrl);
    assertNoPayload(args);
    return agent.getConfigSummary();
  });

  ipcMain.handle(IPC.openConfigFolder, async (event: IpcMainInvokeEvent, ...args: unknown[]): Promise<void> => {
    assertTrustedSender(event, allowedUrl);
    assertNoPayload(args);
    await deps.openConfigFolder();
  });

  return () => {
    for (const channel of Object.values(IPC)) {
      if (channel !== IPC.statusChanged) {
        ipcMain.removeHandler(channel);
      }
    }
  };
}

/** Pushes the latest status to every open agent window. */
export function broadcastStatus(status: AgentStatus): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send(IPC.statusChanged, status);
    }
  }
}
