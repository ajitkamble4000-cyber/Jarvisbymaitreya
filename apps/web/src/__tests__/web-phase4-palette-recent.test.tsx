import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette } from "../layouts/CommandPalette";
import { RECENT_LIMIT, recentCommands } from "../layouts/commandPalette";
import { NAV_ITEMS } from "../layouts/nav";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

function conversation(id: string, title: string) {
  return {
    id,
    title,
    archived: false,
    pinned: false,
    provider_id: null,
    model: null,
    created_at: "2026-10-21T09:00:00Z",
    updated_at: "2026-10-21T09:00:00Z",
    last_message_at: null,
  };
}

const CONVERSATIONS = ["One", "Two", "Three", "Four", "Five", "Six"].map((title, index) => conversation(`c${index + 1}`, title));

describe("recent conversation commands", () => {
  it("offers only the first few conversations, in the order the list gives", () => {
    const commands = recentCommands(CONVERSATIONS);

    expect(commands).toHaveLength(RECENT_LIMIT);
    expect(commands[0]).toMatchObject({ id: "recent.chat.c1", group: "Recent", label: "One", path: "/chat/c1" });
  });

  it("offers nothing while the list has not loaded", () => {
    expect(recentCommands(undefined)).toEqual([]);
  });
});

describe("recent conversations in the palette", () => {
  it("lists the newest conversations before anything is typed", async () => {
    routeFetch({ "/conversations": () => ({ items: CONVERSATIONS, total: CONVERSATIONS.length }) });
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={vi.fn()} />);

    expect(await screen.findByRole("option", { name: /^One/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /^Five/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /^Six/ })).not.toBeInTheDocument();
  });

  it("runs a recent conversation when it is clicked", async () => {
    routeFetch({ "/conversations": () => ({ items: CONVERSATIONS, total: CONVERSATIONS.length }) });
    const onRun = vi.fn();
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={onRun} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("option", { name: /^Two/ }));

    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ path: "/chat/c2" }));
  });

  it("drops the recent list once something is typed", async () => {
    routeFetch({
      "/conversations": () => ({ items: CONVERSATIONS, total: CONVERSATIONS.length }),
      "/search": () => ({ query: "notif", conversations: [], memories: [], tasks: [] }),
    });
    renderWithProviders(<CommandPalette open onClose={() => {}} navItems={NAV_ITEMS} onRun={vi.fn()} />);
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /^One/ });

    await user.type(screen.getByRole("searchbox", { name: "Search pages and actions" }), "notif");

    expect(screen.queryByRole("option", { name: /^One/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Notifications/ })).toBeInTheDocument();
  });
});
