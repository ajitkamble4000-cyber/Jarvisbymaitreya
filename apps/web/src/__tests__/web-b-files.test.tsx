import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FilesPage from "../pages/files/FilesPage";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const device = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Laptop",
  device_type: "laptop",
  os: "windows",
  os_version: "10.0.22631",
  agent_version: "1.0.0",
  pairing_status: "paired",
  connection_status: "online",
  is_default: true,
  capabilities: ["open_url"],
  permissions: {},
  resources: {
    apps: [],
    urls: [],
    folders: [{ id: "docs", label: "Documents" }],
    projects: [{ id: "jarvis", label: "JARVIS repo" }],
    files: [{ id: "notes", label: "Notes.txt" }],
  },
  registered_at: null,
  paired_at: null,
  last_seen_at: null,
  last_connected_at: null,
  revoked_at: null,
};

describe("Files page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists approved labels and has no upload or delete control", async () => {
    routeFetch({ "/devices": () => ({ items: [device] }) });
    renderWithProviders(<FilesPage />, { route: "/files" });

    expect(await screen.findByText("Documents")).toBeInTheDocument();
    expect(screen.getByText("JARVIS repo")).toBeInTheDocument();
    expect(screen.getByText("Notes.txt")).toBeInTheDocument();
    expect(screen.getByText(/Browsing or editing files on the server is not implemented yet/)).toBeInTheDocument();

    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /upload|delete|remove|add|edit/i })).toBeNull();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });
});
