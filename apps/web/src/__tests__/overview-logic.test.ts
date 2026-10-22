import { describe, expect, it } from "vitest";
import { deriveAssistantState, upcomingSchedules, describeAudit } from "../pages/overview/overviewModel";
import { invalidationsForEvent, qk } from "../services/queries";
import { moveNavItem, orderNavItems, visibleNavItems, NAV_ITEMS } from "../layouts/nav";

describe("overview state", () => {
  it("prefers offline, then approvals, then running work, then degraded health", () => {
    const base = { offline: false, awaitingApproval: 0, running: 0, degraded: false };
    expect(deriveAssistantState({ ...base, offline: true, awaitingApproval: 2 })).toBe("disconnected");
    expect(deriveAssistantState({ ...base, awaitingApproval: 1, running: 1, degraded: true })).toBe("awaiting_approval");
    expect(deriveAssistantState({ ...base, running: 1, degraded: true })).toBe("executing");
    expect(deriveAssistantState({ ...base, degraded: true })).toBe("error");
    expect(deriveAssistantState(base)).toBe("idle");
  });

  it("lists only enabled, unpaused schedules, soonest first", () => {
    const schedule = (id: string, next: string | null, extra: Partial<{ enabled: boolean; paused: boolean }> = {}) =>
      ({ id, name: id, enabled: true, paused: false, next_run_at: next, ...extra }) as never;
    const picked = upcomingSchedules([
      schedule("late", "2026-10-20T09:00:00Z"),
      schedule("paused", "2026-10-19T08:00:00Z", { paused: true }),
      schedule("soon", "2026-10-19T10:00:00Z"),
      schedule("none", null),
    ]);
    expect(picked.map((item) => item.id)).toEqual(["soon", "late"]);
  });

  it("describes an audit entry in readable words", () => {
    expect(describeAudit({ action: "device.revoked", target_type: "device" } as never)).toBe("device revoked (device)");
  });
});

describe("live event invalidation", () => {
  it("refetches tasks for task and approval events and conversations for chat events", () => {
    expect(invalidationsForEvent({ type: "task.updated", data: { task_id: "t1", status: "running" } })).toEqual([qk.tasks]);
    expect(invalidationsForEvent({ type: "approval.requested", data: { task_id: "t1", title: "x" } })).toContainEqual(qk.tasks);
    const chat = invalidationsForEvent({ type: "chat.message", data: { conversation_id: "c1", message_id: "m1" } });
    expect(chat).toContainEqual(qk.conversation("c1"));
    expect(invalidationsForEvent({ type: "heartbeat", data: { time: "now" } })).toEqual([]);
  });
});

describe("navigation model", () => {
  it("keeps default order for items the viewer has not placed, and ignores unknown ids", () => {
    const ordered = orderNavItems(["tasks", "nope", "chat"]).map((item) => item.id);
    expect(ordered.slice(0, 2)).toEqual(["tasks", "chat"]);
    expect(ordered).toHaveLength(NAV_ITEMS.length);
  });

  it("moves an item up or down and does nothing at the edges", () => {
    expect(moveNavItem(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(moveNavItem(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
    expect(moveNavItem(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
  });

  it("hides items the viewer switched off but keeps the rest in order", () => {
    const visible = visibleNavItems(["tasks", "chat"], ["chat"]).map((item) => item.id);
    expect(visible[0]).toBe("tasks");
    expect(visible).not.toContain("chat");
  });
});
