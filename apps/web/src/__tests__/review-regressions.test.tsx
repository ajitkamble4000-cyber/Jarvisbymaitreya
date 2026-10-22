import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantStatusCard } from "../pages/overview/cards";
import { emergencyStopMessage } from "../pages/automation/automationQueries";
import { ToolProposalCard } from "../pages/chat/ToolProposalCard";
import { describeTask, isUnconfirmedResult } from "../pages/tasks/taskModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch, taskFixture } from "./helpers";

function withResult(task: ReturnType<typeof taskFixture>, output: unknown, summary = "Started Google Chrome (pid 4242).") {
  return { ...task, result: { summary, output } } as never;
}

describe("emergency stop wording", () => {
  it("reports running tasks as asked to stop, not as cancelled", () => {
    expect(emergencyStopMessage({ cancelled: 2, cancel_requested: 1 })).toBe(
      "2 tasks cancelled. 1 running task asked to stop on the device.",
    );
    expect(emergencyStopMessage({ cancelled: 1, cancel_requested: 0 })).toBe("1 task cancelled.");
  });
});

describe("task wording does not overclaim", () => {
  it("shows a failed task's message without the internal error code", () => {
    const failed = { ...taskFixture("t1", "failed"), error: { code: "agent_error", message: "The app did not start.", details: {} } };
    expect(describeTask(failed as never)).toBe("The app did not start.");
  });

  it("marks a completed task whose output is not verified as not confirmed", () => {
    const unverified = withResult(taskFixture("t2", "completed"), { verified: false, summary: "Started" });
    expect(isUnconfirmedResult(unverified)).toBe(true);
    expect(describeTask(unverified)).toMatch(/^Not confirmed: /);
  });

  it("treats a cancel that is still running on the device as not yet confirmed", () => {
    const cancel = {
      ...taskFixture("t3", "completed"),
      tool_name: "cancel_task",
      result: { summary: "Cancel requested.", output: { status: "running" } },
    };
    expect(isUnconfirmedResult(cancel as never)).toBe(true);
  });

  it("leaves a verified completion as it was", () => {
    const done = withResult(taskFixture("t4", "completed"), { verified: true }, "Opened the page.");
    expect(isUnconfirmedResult(done)).toBe(false);
    expect(describeTask(done)).toBe("Opened the page.");
  });
});

describe("overview backend status", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the backend as unreachable when the latest health request failed, even with older data", () => {
    renderWithProviders(
      <AssistantStatusCard health={{ status: "ok", database: "ok" } as never} unreachable provider={undefined} settings={undefined} />,
      { route: "/" },
    );
    expect(screen.getByText(/Backend: Unreachable/)).toBeInTheDocument();
    expect(screen.queryByText(/Backend: Healthy/)).toBeNull();
  });
});

describe("chat approval card", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stops offering Approve once the approval has succeeded", async () => {
    routeFetch({ "/tasks/t9/approve": () => taskFixture("t9", "running") });
    const user = userEvent.setup();
    renderWithProviders(<ToolProposalCard task={taskFixture("t9", "awaiting_approval") as never} />, { route: "/voice" });

    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeEnabled();
    await user.click(approve);

    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled());
    expect(screen.queryByText("Waiting for approval")).toBeNull();
  });
});
