/**
 * Preload for the quick-ask popup. It exposes one function, window.jarvisQuick.hide(), and nothing else. The popup
 * shows a web page from the JARVIS web app, so it gets the smallest bridge possible.
 * Bundled to a single file by scripts/build-bundles.mjs, like the agent preload.
 */
import { contextBridge, ipcRenderer } from "electron";
import { IPC } from "../shared/ipc-contract.js";

contextBridge.exposeInMainWorld("jarvisQuick", {
  hide: (): void => {
    ipcRenderer.send(IPC.quickHide);
  },
});
