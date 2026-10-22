import { afterEach, describe, expect, it, vi } from "vitest";
import { browserAlertFor, requestBrowserPermission, showBrowserAlert, type AlertSettings } from "../services/browserNotifications";
import { ApiError } from "../services/api";
import { downloadConversationExport, fileNameFromDisposition } from "../services/exportDownload";
import { promptFromSearch, PROMPT_MAX_LENGTH } from "../pages/chat/deepLink";
import { effectiveSettingsTab, parseSettingsTab, settingsTabsFor } from "../pages/settings/settingsModel";
import { canManageUser, lastSignInText, userStatusText, validateMember, validatePassword } from "../pages/settings/usersModel";
import { pushModeFor } from "../pages/voice/voiceModel";
import type { AdminUser, LiveEvent } from "../types/api";

const allOn: AlertSettings = {
  desktop_notifications: true,
  on_approval_required: true,
  on_task_completed: true,
  on_task_failed: true,
  include_task_details: false,
};
const hidden = { hidden: true };

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("browser alert rules", () => {
  const approval: LiveEvent = { type: "approval.requested", data: { task_id: "t1", title: "Open Chrome on laptop" } };
  const completed: LiveEvent = { type: "task.updated", data: { task_id: "t2", status: "completed" } };
  const failed: LiveEvent = { type: "task.updated", data: { task_id: "t3", status: "failed" } };

  it("makes no alert while the tab is visible", () => {
    expect(browserAlertFor(approval, allOn, { hidden: false })).toBeNull();
    expect(browserAlertFor(completed, allOn, { hidden: false })).toBeNull();
  });

  it("makes no alert when desktop notifications are off", () => {
    expect(browserAlertFor(approval, { ...allOn, desktop_notifications: false }, hidden)).toBeNull();
  });

  it("uses generic text by default", () => {
    const alert = browserAlertFor(approval, allOn, hidden);
    expect(alert?.title).toBe("Approval needed");
    expect(alert?.body).not.toContain("Open Chrome");
  });

  it("includes task details only when include_task_details is on", () => {
    const alert = browserAlertFor(approval, { ...allOn, include_task_details: true }, hidden);
    expect(alert?.body).toContain("Open Chrome on laptop");
  });

  it("respects each event flag", () => {
    expect(browserAlertFor(approval, { ...allOn, on_approval_required: false }, hidden)).toBeNull();
    expect(browserAlertFor(completed, { ...allOn, on_task_completed: false }, hidden)).toBeNull();
    expect(browserAlertFor(failed, { ...allOn, on_task_failed: false }, hidden)).toBeNull();
    expect(browserAlertFor(failed, allOn, hidden)?.title).toBe("Task failed");
    expect(browserAlertFor(completed, allOn, hidden)?.title).toBe("Task completed");
  });

  it("ignores task updates that are not finished", () => {
    const running: LiveEvent = { type: "task.updated", data: { task_id: "t4", status: "running" } };
    expect(browserAlertFor(running, allOn, hidden)).toBeNull();
  });

  it("ignores other event kinds", () => {
    const device: LiveEvent = { type: "device.updated", data: { device_id: "d", connection_status: "online" } };
    expect(browserAlertFor(device, allOn, hidden)).toBeNull();
  });

  it("uses a task title the page already knows, when details are allowed", () => {
    const alert = browserAlertFor(completed, { ...allOn, include_task_details: true }, { hidden: true, taskTitle: () => "Daily report" });
    expect(alert?.body).toBe("Daily report");
  });

  it("gives each task one tag so a repeat replaces the alert", () => {
    expect(browserAlertFor(failed, allOn, hidden)?.tag).toBe("task:t3");
  });

  it("shows an alert only when permission is granted, and never throws", () => {
    const created: Array<{ title: string; options: NotificationOptions }> = [];
    class FakeNotification {
      static permission = "granted";
      constructor(title: string, options: NotificationOptions) {
        created.push({ title, options });
      }
    }
    vi.stubGlobal("Notification", FakeNotification);
    expect(showBrowserAlert({ title: "Task failed", body: "Open JARVIS to see the result.", tag: "task:t3" })).toBe(true);
    expect(created).toEqual([{ title: "Task failed", options: { body: "Open JARVIS to see the result.", tag: "task:t3" } }]);

    FakeNotification.permission = "denied";
    expect(showBrowserAlert({ title: "x", body: "y", tag: "z" })).toBe(false);
    expect(created).toHaveLength(1);
  });

  it("asks for permission and reports the answer in words", async () => {
    const requestPermission = vi.fn(async () => "denied");
    vi.stubGlobal("Notification", Object.assign(function () {}, { permission: "default", requestPermission }));
    await expect(requestBrowserPermission()).resolves.toBe("denied");
    expect(requestPermission).toHaveBeenCalledTimes(1);
  });

  it("reports an unsupported browser instead of failing", async () => {
    vi.stubGlobal("Notification", undefined);
    await expect(requestBrowserPermission()).resolves.toBe("unsupported");
  });
});

describe("export downloads", () => {
  it("keeps only safe characters from the server's file name", () => {
    expect(fileNameFromDisposition('attachment; filename="Plan 2027_v1.md"', "x.md")).toBe("Plan 2027_v1.md");
    expect(fileNameFromDisposition('attachment; filename="../../etc/passwd"', "x.md")).toBe("etcpasswd");
    expect(fileNameFromDisposition('attachment; filename=".hidden"', "x.md")).toBe("hidden");
  });

  it("falls back when no file name is given", () => {
    expect(fileNameFromDisposition(null, "conversation.md")).toBe("conversation.md");
    expect(fileNameFromDisposition('attachment; filename=""', "conversation.md")).toBe("conversation.md");
  });

  it("downloads through a blob URL, then revokes it", async () => {
    vi.useFakeTimers();
    const body = "# Plan\n";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Disposition": 'attachment; filename="Plan.md"' } })),
    );
    const createObjectURL = vi.fn(() => "blob:jarvis-test");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const name = await downloadConversationExport("conv-1", "markdown");

    expect(name).toBe("Plan.md");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:jarvis-test");
    click.mockRestore();
  });

  it("requests the export route with the format and the session cookie", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await downloadConversationExport("abc", "json");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/conversations/abc/export?format=json");
    expect(init.credentials).toBe("include");
    click.mockRestore();
  });

  it("turns a failed export into the server's message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "not_found", message: "Not found." } }), { status: 404 })),
    );
    const error = await downloadConversationExport("missing", "markdown").catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("not_found");
    expect((error as ApiError).message).toBe("Not found.");
  });
});

describe("deep link prompts", () => {
  it("returns nothing when the link has no prompt or only spaces", () => {
    expect(promptFromSearch("")).toEqual({ text: "", truncated: false });
    expect(promptFromSearch("?prompt=%20%20")).toEqual({ text: "", truncated: false });
  });

  it("decodes the prompt", () => {
    expect(promptFromSearch("?prompt=Summarise%20my%20day%3F")).toEqual({ text: "Summarise my day?", truncated: false });
  });

  it("caps the prompt at 2000 characters and says so", () => {
    const long = "a".repeat(PROMPT_MAX_LENGTH + 50);
    const link = promptFromSearch(`?prompt=${encodeURIComponent(long)}`);
    expect(link.text).toHaveLength(PROMPT_MAX_LENGTH);
    expect(link.truncated).toBe(true);
  });

  it("does not cut a prompt that fits exactly", () => {
    const exact = "b".repeat(PROMPT_MAX_LENGTH);
    expect(promptFromSearch(`?prompt=${exact}`)).toEqual({ text: exact, truncated: false });
  });
});

describe("settings tabs by role", () => {
  it("shows the Users tab to the owner only", () => {
    expect(settingsTabsFor(true).map((tab) => tab.id)).toContain("users");
    expect(settingsTabsFor(false).map((tab) => tab.id)).not.toContain("users");
  });

  it("sends a member who opens the Users tab to the AI tab", () => {
    expect(effectiveSettingsTab(parseSettingsTab("users"), false)).toBe("ai");
    expect(effectiveSettingsTab(parseSettingsTab("users"), true)).toBe("users");
    expect(effectiveSettingsTab(parseSettingsTab("voice"), false)).toBe("voice");
  });
});

describe("member account rules", () => {
  const member: AdminUser = {
    id: "u1",
    email: "sam@example.com",
    display_name: "Sam",
    role: "member",
    is_active: true,
    created_at: "2026-10-21T09:00:00Z",
    last_login_at: null,
  };

  it("checks the password length the server checks", () => {
    expect(validatePassword("short")).toMatch(/at least 12/);
    expect(validatePassword("x".repeat(12))).toBeNull();
    expect(validatePassword("x".repeat(257))).toMatch(/256/);
  });

  it("validates the create form", () => {
    expect(validateMember({ email: "not-an-email", displayName: "Sam", password: "long-enough-pass" })).toHaveProperty("email");
    expect(validateMember({ email: " Sam@Example.com ", displayName: "  ", password: "long-enough-pass" })).toHaveProperty("displayName");
    expect(validateMember({ email: "sam@example.com", displayName: "Sam\u0007", password: "long-enough-pass" })).toHaveProperty("displayName");
    expect(validateMember({ email: "sam@example.com", displayName: "Sam", password: "short" })).toHaveProperty("password");
    expect(validateMember({ email: " Sam@Example.com ", displayName: "Sam", password: "long-enough-pass" })).toEqual({});
  });

  it("describes status in words and offers no actions for the owner", () => {
    expect(userStatusText(member)).toBe("Active");
    expect(userStatusText({ ...member, is_active: false })).toBe("Deactivated");
    expect(userStatusText({ ...member, role: "owner" })).toBe("Owner account");
    expect(canManageUser(member)).toBe(true);
    expect(canManageUser({ ...member, role: "owner" })).toBe(false);
    expect(lastSignInText(member)).toBe("Has not signed in yet");
  });
});

describe("push to talk mode", () => {
  it("is hold to talk when the setting is on or missing, and toggle when it is off", () => {
    expect(pushModeFor(true)).toBe("hold");
    expect(pushModeFor(undefined)).toBe("hold");
    expect(pushModeFor(false)).toBe("toggle");
  });
});
