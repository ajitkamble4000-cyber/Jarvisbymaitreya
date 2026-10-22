/**
 * Renderer: pairing and status UI. Plain TypeScript, no framework. It talks only to window.jarvis, and every
 * dynamic value is written with textContent, so nothing from the backend is ever parsed as markup.
 */
import type { AgentStatus, ConfigSummary, ResourceView } from "../shared/ipc-contract.js";

const api = window.jarvis;

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`Missing element: ${id}`);
  }
  return element as T;
}

const els = {
  banner: byId<HTMLElement>("status-banner"),
  dot: byId<HTMLElement>("status-dot"),
  state: byId<HTMLElement>("status-state"),
  message: byId<HTMLElement>("status-message"),
  device: byId<HTMLElement>("status-device"),
  server: byId<HTMLElement>("status-server"),
  unpair: byId<HTMLButtonElement>("unpair-button"),
  openFolder: byId<HTMLButtonElement>("open-folder-button"),
  actionMessage: byId<HTMLElement>("action-message"),
  pairForm: byId<HTMLFormElement>("pair-form"),
  serverUrl: byId<HTMLInputElement>("server-url"),
  code: byId<HTMLInputElement>("pair-code"),
  deviceName: byId<HTMLInputElement>("device-name"),
  pairButton: byId<HTMLButtonElement>("pair-button"),
  pairMessage: byId<HTMLElement>("pair-message"),
  apps: byId<HTMLElement>("apps"),
  urls: byId<HTMLElement>("urls"),
  folders: byId<HTMLElement>("folders"),
  projects: byId<HTMLElement>("projects"),
  files: byId<HTMLElement>("files"),
  capabilities: byId<HTMLElement>("capabilities"),
};

const STATE_TEXT: Record<AgentStatus["state"], string> = {
  connecting: "Connecting",
  connected: "Connected",
  offline: "Offline, reconnecting",
  paired: "Paired, not running",
  unpaired: "Not paired",
  "re-pair-required": "Pair again required",
  error: "Error",
};

let unpairArmed = false;
let unpairTimer: number | undefined;

function setText(element: HTMLElement, text: string): void {
  element.textContent = text;
}

function renderStatus(status: AgentStatus): void {
  setText(els.state, STATE_TEXT[status.state]);
  els.dot.dataset.state = status.state;
  setText(els.message, status.message ?? "");
  setText(els.device, status.deviceName ?? "Not paired");
  setText(els.server, status.serverHost ?? "None");
  els.unpair.disabled = status.state === "unpaired";
  if (status.banner !== null) {
    setText(els.banner, status.banner);
    els.banner.hidden = false;
  } else {
    els.banner.hidden = true;
  }
}

function renderList(list: HTMLElement, entries: ResourceView[], emptyText: string): void {
  list.replaceChildren();
  if (entries.length === 0) {
    const item = document.createElement("li");
    item.className = "empty";
    setText(item, emptyText);
    list.appendChild(item);
    return;
  }
  for (const entry of entries) {
    const item = document.createElement("li");
    setText(item, entry.label);
    const id = document.createElement("span");
    id.className = "id";
    setText(id, entry.id);
    item.appendChild(id);
    list.appendChild(item);
  }
}

function renderConfig(summary: ConfigSummary): void {
  renderList(els.apps, summary.apps, "None approved");
  renderList(els.urls, summary.urls, "None approved");
  renderList(els.folders, summary.folders, "None approved");
  renderList(els.projects, summary.projects, "None approved");
  renderList(els.files, summary.files, "None approved");
  els.capabilities.replaceChildren();
  for (const capability of summary.capabilities) {
    const chip = document.createElement("li");
    setText(chip, capability.replace(/_/g, " "));
    els.capabilities.appendChild(chip);
  }
}

function describeError(error: unknown): string {
  return error instanceof Error && error.message.length < 200 ? error.message : "Something went wrong.";
}

async function refreshConfig(): Promise<void> {
  try {
    renderConfig(await api.getConfigSummary());
  } catch (error) {
    setText(els.actionMessage, describeError(error));
  }
}

els.pairForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const serverUrl = els.serverUrl.value.trim();
  const code = els.code.value.trim();
  const deviceName = els.deviceName.value.trim();
  if (serverUrl.length === 0 || code.length === 0) {
    setText(els.pairMessage, "Enter the server address and the pairing code.");
    return;
  }
  els.pairButton.disabled = true;
  setText(els.pairMessage, "Pairing...");
  api
    .pair(deviceName.length > 0 ? { serverUrl, code, deviceName } : { serverUrl, code })
    .then(async (result) => {
      if (result.ok) {
        // The code is single use. Clear it as soon as the server has accepted it.
        els.code.value = "";
        setText(els.pairMessage, "Paired. Connecting...");
        renderStatus(result.status);
        await refreshConfig();
      } else {
        setText(els.pairMessage, result.message);
      }
    })
    .catch((error: unknown) => {
      setText(els.pairMessage, describeError(error));
    })
    .finally(() => {
      els.pairButton.disabled = false;
    });
});

// Unpairing needs two clicks within five seconds, so one stray click cannot remove the pairing.
els.unpair.addEventListener("click", () => {
  if (!unpairArmed) {
    unpairArmed = true;
    setText(els.unpair, "Click again to unpair");
    unpairTimer = window.setTimeout(disarmUnpair, 5000);
    return;
  }
  disarmUnpair();
  els.unpair.disabled = true;
  api
    .unpair()
    .then(async (status) => {
      renderStatus(status);
      setText(els.actionMessage, "Unpaired on this PC. Revoke the device in the web app as well.");
      await refreshConfig();
    })
    .catch((error: unknown) => {
      setText(els.actionMessage, describeError(error));
    })
    .finally(() => {
      els.unpair.disabled = false;
    });
});

function disarmUnpair(): void {
  unpairArmed = false;
  if (unpairTimer !== undefined) {
    window.clearTimeout(unpairTimer);
    unpairTimer = undefined;
  }
  setText(els.unpair, "Unpair");
}

els.openFolder.addEventListener("click", () => {
  api
    .openConfigFolder()
    .catch((error: unknown) => {
      setText(els.actionMessage, describeError(error));
    });
});

api.onStatus(renderStatus);

api
  .getStatus()
  .then(renderStatus)
  .catch((error: unknown) => {
    setText(els.message, describeError(error));
  });
refreshConfig().catch(() => undefined);
