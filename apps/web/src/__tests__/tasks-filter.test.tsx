import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TasksPage from "../pages/tasks/TasksPage";
import { buildTaskQuery, countForFilter, describeTask, taskActionStates, totalCount, TASK_FILTERS } from "../pages/tasks/taskModel";
import { buildQuery } from "../services/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch, taskFixture } from "./helpers";

const counts = { queued: 1, running: 0, awaiting_approval: 1, completed: 2, failed: 0, cancelled: 0 };

describe("task filter logic", () => {
  it("builds the list query for a tab and search, dropping the all filter and blank search", () => {
    expect(buildTaskQuery("all", "  ")).toEqual({ status: "all", limit: 50 });
    expect(buildTaskQuery("awaiting_approval", "  chrome ")).toEqual({ status: "awaiting_approval", q: "chrome", limit: 50 });
    expect(buildQuery({ status: "awaiting_approval", q: "chrome", limit: 50 })).toBe("?status=awaiting_approval&q=chrome&limit=50");
  });

  it("counts a tab from the API counts, and All as the sum of every status", () => {
    expect(countForFilter(counts, "awaiting_approval")).toBe(1);
    expect(countForFilter(counts, "completed")).toBe(2);
    expect(countForFilter(counts, "all")).toBe(totalCount(counts));
    expect(countForFilter(undefined, "all")).toBe(0);
    expect(TASK_FILTERS.map((filter) => filter.id)).toContain("awaiting_approval");
  });

  it("enables each action only when the API allows it and nothing else is running", () => {
    const awaiting = taskFixture("t1", "awaiting_approval") as never;
    expect(taskActionStates(awaiting, null)).toMatchObject({
      approve: { enabled: true },
      deny: { enabled: true },
      cancel: { enabled: false },
      retry: { enabled: false },
    });
    expect(taskActionStates(awaiting, "approve")).toMatchObject({ approve: { enabled: false }, deny: { enabled: false } });
  });

  it("says a queued task is waiting for its device, honestly", () => {
    const queued = { ...taskFixture("t2", "queued"), current_step: "waiting_for_device", device: { id: "d", name: "Laptop", connection_status: "offline" } } as never;
    expect(describeTask(queued)).toBe("Waiting for Laptop to come online. Nothing has run yet.");
  });
});

describe("Tasks page filtering", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows counts on the tabs and requests the filtered list when a tab is chosen", async () => {
    const { calls } = routeFetch({
      "/tasks": (url) => {
        const status = url.searchParams.get("status");
        const items = status === "awaiting_approval" ? [taskFixture("t1", "awaiting_approval", "Open Chrome")] : [taskFixture("t1", "awaiting_approval", "Open Chrome")];
        return { items, total: items.length, counts };
      },
    });
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />, { route: "/tasks" });

    const tabs = await screen.findByRole("tablist", { name: "Filter tasks by status" });
    const awaitingTab = within(tabs).getByRole("tab", { name: /Awaiting approval/ });
    await waitFor(() => expect(awaitingTab).toHaveTextContent("1"));

    await user.click(awaitingTab);
    expect(awaitingTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(calls).toContain("/tasks?status=awaiting_approval&limit=50"));
  });

  it("offers Approve and Deny for a task that is awaiting approval", async () => {
    routeFetch({
      "/tasks": () => ({ items: [taskFixture("t9", "awaiting_approval", "Launch Notepad")], total: 1, counts }),
    });
    renderWithProviders(<TasksPage />, { route: "/tasks" });
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeEnabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
  });
});

describe("status URL parameter", () => {
  it("reads a known status and falls back to All for anything else", async () => {
    const { taskFilterFromParam } = await import("../pages/tasks/taskModel");
    expect(taskFilterFromParam("awaiting_approval")).toBe("awaiting_approval");
    expect(taskFilterFromParam("bogus")).toBe("all");
    expect(taskFilterFromParam(null)).toBe("all");
  });
});
