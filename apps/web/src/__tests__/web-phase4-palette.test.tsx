import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette } from "../layouts/CommandPalette";
import { buildCommands, filterCommands, isPaletteShortcut, searchCommands, stepIndex } from "../layouts/commandPalette";
import { NAV_ITEMS } from "../layouts/nav";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

describe("command palette rules", () => {
  const commands = buildCommands(NAV_ITEMS);

  it("lists every page and then the actions", () => {
    expect(commands.filter((command) => command.group === "Go to")).toHaveLength(NAV_ITEMS.length);
    expect(commands.some((command) => command.id === "action.new-task" && command.path === "/tasks?create=1")).toBe(true);
  });

  it("matches every word of the query, ignoring case, in labels and keywords", () => {
    expect(filterCommands(commands, "CHAT").map((command) => command.label)).toEqual(expect.arrayContaining(["AI Chat", "New chat"]));
    const voice = filterCommands(commands, "voice settings").map((command) => command.label);
    expect(voice).toEqual(["Voice settings"]);
    expect(filterCommands(commands, "quiet hours").map((command) => command.id)).toEqual(["action.notification-settings"]);
  });

  it("keeps everything for a blank query and nothing for an unknown one", () => {
    expect(filterCommands(commands, "   ")).toHaveLength(commands.length);
    expect(filterCommands(commands, "zzz-no-match")).toEqual([]);
  });

  it("wraps the highlight at both ends and is zero with no items", () => {
    expect(stepIndex(0, -1, 3)).toBe(2);
    expect(stepIndex(2, 1, 3)).toBe(0);
    expect(stepIndex(1, 1, 3)).toBe(2);
    expect(stepIndex(0, 1, 0)).toBe(0);
  });

  it("opens on Ctrl+K or Cmd+K only", () => {
    expect(isPaletteShortcut({ key: "k", ctrlKey: true, metaKey: false })).toBe(true);
    expect(isPaletteShortcut({ key: "K", ctrlKey: false, metaKey: true })).toBe(true);
    expect(isPaletteShortcut({ key: "k", ctrlKey: false, metaKey: false })).toBe(false);
    expect(isPaletteShortcut({ key: "j", ctrlKey: true, metaKey: false })).toBe(false);
  });
});

describe("command palette", () => {
  it("runs the first match on Enter", async () => {
    const onRun = vi.fn();
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={onRun} />);
    const user = userEvent.setup();

    await user.type(screen.getByRole("searchbox", { name: "Search pages and actions" }), "notif");
    await user.keyboard("{Enter}");

    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ id: "go.notifications", path: "/notifications" }));
  });

  it("moves the highlight with the arrow keys before Enter", async () => {
    const onRun = vi.fn();
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={onRun} />);
    const user = userEvent.setup();
    const search = screen.getByRole("searchbox", { name: "Search pages and actions" });

    await user.type(search, "notif");
    await user.keyboard("{ArrowDown}{Enter}");

    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ id: "action.notification-settings" }));
  });

  it("runs a command when it is clicked and says so when nothing matches", async () => {
    const onRun = vi.fn();
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={onRun} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("option", { name: /Pair a desktop agent/ }));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ path: "/devices?pair=1" }));

    await user.type(screen.getByRole("searchbox", { name: "Search pages and actions" }), "zzz");
    expect(screen.getByRole("status")).toHaveTextContent("Nothing matches");
  });
});

describe("search hits in the palette", () => {
  const result = {
    query: "holiday",
    conversations: [{ id: "c1", title: "Holiday plans", match: "title" as const, updated_at: "2026-10-21T09:00:00Z" }],
    memories: [{ id: "m1", category: "preference", excerpt: "Prefers morning meetings" }],
    tasks: [{ id: "t1", title: "Holiday packing list", tool_name: "list_devices", status: "completed" as const }],
  };

  it("turns each hit into a command that opens its place", () => {
    const commands = searchCommands(result);
    expect(commands.map((command) => [command.group, command.path])).toEqual([
      ["Found", "/chat/c1"],
      ["Found", "/tasks/t1"],
      ["Found", "/memory"],
    ]);
    expect(searchCommands(undefined)).toEqual([]);
  });

  it("finds a conversation by typed text and opens it on Enter", async () => {
    const onRun = vi.fn();
    routeFetch({ "/search": () => result });
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={onRun} />);
    const user = userEvent.setup();

    await user.type(screen.getByRole("searchbox", { name: "Search pages and actions" }), "holiday");
    expect(await screen.findByRole("option", { name: /Holiday plans/ }, { timeout: 3000 })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /Holiday plans/ }));

    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ path: "/chat/c1" }));
  });
});
