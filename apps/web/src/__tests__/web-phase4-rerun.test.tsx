import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TaskDetail } from "../pages/tasks/TaskDetail";
import { canRunAgain } from "../pages/tasks/taskModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch, taskFixture } from "./helpers";

describe("run again", () => {
  it("is offered only for finished tasks", () => {
    expect(canRunAgain("completed")).toBe(true);
    expect(canRunAgain("failed")).toBe(true);
    expect(canRunAgain("cancelled")).toBe(true);
    expect(canRunAgain("queued")).toBe(false);
    expect(canRunAgain("running")).toBe(false);
    expect(canRunAgain("awaiting_approval")).toBe(false);
  });

  it("creates a new task with the same tool, arguments and title", async () => {
    const posted: unknown[] = [];
    const original = { ...taskFixture("t1", "completed", "Open Chrome"), arguments: { app: "chrome" } };
    routeFetch({
      "/tasks/t1": () => ({ task: original, events: [] }),
      "/tasks": (_url, init) => {
        if (init?.method === "POST") {
          posted.push(JSON.parse(String(init.body)));
          return taskFixture("t2", "queued", "Open Chrome");
        }
        return { items: [], total: 0, counts: {} };
      },
    });
    renderWithProviders(<TaskDetail taskId="t1" />, { route: "/tasks/t1", path: "/tasks/:taskId" });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Run again" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      tool_name: "launch_app",
      arguments: { app: "chrome" },
      title: "Open Chrome",
      description: "",
    });
  });

  it("is not shown for a task that is still waiting for approval", async () => {
    routeFetch({ "/tasks/t3": () => ({ task: taskFixture("t3", "awaiting_approval"), events: [] }) });
    renderWithProviders(<TaskDetail taskId="t3" />, { route: "/tasks/t3", path: "/tasks/:taskId" });
    await screen.findByRole("button", { name: "Approve" });
    expect(screen.queryByRole("button", { name: "Run again" })).not.toBeInTheDocument();
  });
});
