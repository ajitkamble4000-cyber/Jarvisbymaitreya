import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AutomationSection } from "../pages/settings/AutomationSection";
import { UsersSection } from "../pages/settings/UsersSection";
import { Composer } from "../pages/chat/Composer";
import { ExportButtons } from "../pages/chat/ExportButtons";
import NotificationsPage from "../pages/notifications/NotificationsPage";
import { renderWithProviders } from "../test/utils";
import type { AdminUser } from "../types/api";
import { routeFetch } from "./helpers";

const owner: AdminUser = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "owner@example.com",
  display_name: "Owner",
  role: "owner",
  is_active: true,
  created_at: "2026-10-19T09:00:00Z",
  last_login_at: "2026-10-21T08:00:00Z",
};

const member: AdminUser = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "sam@example.com",
  display_name: "Sam",
  role: "member",
  is_active: true,
  created_at: "2026-10-20T09:00:00Z",
  last_login_at: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("notifications inbox", () => {
  it("says how many notifications the settings hide, with a link to the settings", async () => {
    routeFetch({
      "/notifications": () => ({ items: [], unread_count: 0, hidden_by_preferences: 2 }),
    });
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });

    expect(await screen.findByText("2 notifications are hidden by your settings.")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Change notification settings" });
    expect(link).toHaveAttribute("href", "/settings?tab=notifications");
  });

  it("shows no note when nothing is hidden", async () => {
    routeFetch({
      "/notifications": () => ({ items: [], unread_count: 0, hidden_by_preferences: 0 }),
    });
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    await screen.findByText("No notifications yet");
    expect(screen.queryByText(/hidden by your settings/)).not.toBeInTheDocument();
  });
});

describe("chat export buttons", () => {
  it("requests the Markdown export and reports the saved file", async () => {
    const createObjectURL = vi.fn(() => "blob:export");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { mock } = routeFetch({
      "/conversations/c1/export": () => ({ ok: true }),
    });
    renderWithProviders(<ExportButtons conversationId="c1" />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Export Markdown" }));

    expect(await screen.findByText("Export saved")).toBeInTheDocument();
    expect(String(mock.mock.calls[0]?.[0])).toBe("/api/v1/conversations/c1/export?format=markdown");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
  });

  it("offers JSON as well", async () => {
    renderWithProviders(<ExportButtons conversationId="c1" />);
    expect(screen.getByRole("button", { name: "Export JSON" })).toBeEnabled();
  });
});

describe("composer prefill from a link", () => {
  it("shows the text in the box and does not send it", async () => {
    const onSend = vi.fn();
    renderWithProviders(<Composer disabled={false} streaming={false} onSend={onSend} onStop={() => {}} initialValue="Plan my week" />);
    const box = screen.getByRole("textbox");
    expect(box).toHaveValue("Plan my week");
    expect(onSend).not.toHaveBeenCalled();

    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith("Plan my week");
  });
});

describe("owner Users section", () => {
  it("lists the accounts and offers actions only for members", async () => {
    routeFetch({ "/users": () => ({ items: [owner, member] }) });
    renderWithProviders(<UsersSection />);

    const list = await screen.findByRole("list", { name: "Accounts" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    const ownerRow = rows.find((row) => row.textContent?.includes("owner@example.com")) as HTMLElement;
    const memberRow = rows.find((row) => row.textContent?.includes("sam@example.com")) as HTMLElement;
    expect(within(ownerRow).queryByRole("button", { name: /Deactivate|Reactivate|Reset password/ })).not.toBeInTheDocument();
    expect(within(memberRow).getByRole("button", { name: "Deactivate" })).toBeInTheDocument();
    expect(within(memberRow).getByRole("button", { name: "Reset password" })).toBeInTheDocument();
    expect(within(memberRow).getByText("Has not signed in yet")).toBeInTheDocument();
  });

  it("explains a bad form before sending, and shows the new password once after a member is created", async () => {
    const posted: unknown[] = [];
    routeFetch({
      "/users": (_url, init) => {
        if (init?.method === "POST") {
          posted.push(JSON.parse(String(init.body)));
          return { ...member, id: "33333333-3333-4333-8333-333333333333" };
        }
        return { items: [owner] };
      },
    });
    renderWithProviders(<UsersSection />);
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "not-an-email");
    await user.type(screen.getByLabelText("Display name"), "Sam");
    await user.type(screen.getByLabelText("Initial password"), "short");
    await user.click(screen.getByRole("button", { name: "Create member" }));
    expect(await screen.findByText(/Enter an email address/)).toBeInTheDocument();
    expect(posted).toHaveLength(0);

    await user.clear(screen.getByLabelText("Email"));
    await user.type(screen.getByLabelText("Email"), "sam@example.com");
    await user.clear(screen.getByLabelText("Initial password"));
    await user.type(screen.getByLabelText("Initial password"), "a-long-first-password");
    await user.click(screen.getByRole("button", { name: "Create member" }));

    const status = await screen.findByRole("status");
    expect(within(status).getByText("Member created: sam@example.com")).toBeInTheDocument();
    expect(within(status).getByText("a-long-first-password")).toBeInTheDocument();
    expect(posted).toEqual([{ email: "sam@example.com", display_name: "Sam", password: "a-long-first-password", role: "member" }]);
  });
});

describe("automation settings save only changed keys", () => {
  it("sends the timeout alone when only the timeout changed", async () => {
    const patches: unknown[] = [];
    routeFetch({
      "/tools": () => [],
      "/settings": (_url, init) => {
        if (init?.method === "PATCH") patches.push(JSON.parse(String(init.body)));
        return { automation: {} };
      },
    });
    renderWithProviders(
      <AutomationSection
        automation={{ max_concurrent_tasks: 3, default_task_timeout_seconds: 300, disabled_tools: [], emergency_stopped: false }}
        timezone={null}
      />,
    );
    const user = userEvent.setup();
    const timeout = await screen.findByLabelText(/Default task timeout/);
    await user.clear(timeout);
    await user.type(timeout, "60");

    const saves = screen.getAllByRole("button", { name: "Save changes" });
    await user.click(saves.find((button) => !(button as HTMLButtonElement).disabled) as HTMLElement);

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ automation: { default_task_timeout_seconds: 60 } });
  });

  it("rejects a timeout outside 10 to 1800 without saving", async () => {
    const patches: unknown[] = [];
    routeFetch({
      "/tools": () => [],
      "/settings": (_url, init) => {
        if (init?.method === "PATCH") patches.push(JSON.parse(String(init.body)));
        return { automation: {} };
      },
    });
    renderWithProviders(
      <AutomationSection
        automation={{ max_concurrent_tasks: 3, default_task_timeout_seconds: 300, disabled_tools: [], emergency_stopped: false }}
        timezone={null}
      />,
    );
    const user = userEvent.setup();
    const timeout = await screen.findByLabelText(/Default task timeout/);
    await user.clear(timeout);
    await user.type(timeout, "5");
    await user.click(screen.getAllByRole("button", { name: "Save changes" }).find((button) => !(button as HTMLButtonElement).disabled) as HTMLElement);

    expect(await screen.findByText("Enter a task timeout in seconds from 10 to 1800.")).toBeInTheDocument();
    expect(patches).toHaveLength(0);
  });
});
