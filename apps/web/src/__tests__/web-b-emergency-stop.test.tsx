import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmergencyStopControl } from "../pages/automation/EmergencyStopControl";
import { EMERGENCY_STOP_PHRASE } from "../pages/automation/automationQueries";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

describe("Emergency stop", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the word STOP as its confirmation", () => {
    expect(EMERGENCY_STOP_PHRASE).toBe("STOP");
  });

  it("requires STOP to be typed before the stop button works, then sends it to the API", async () => {
    const { mock } = routeFetch({ "/tasks/emergency-stop": () => ({ stopped: true, cancelled: 2 }) });
    const user = userEvent.setup();
    renderWithProviders(<EmergencyStopControl stopped={false} />, { route: "/automation" });

    await user.click(screen.getByRole("button", { name: "Emergency stop" }));
    const confirm = screen.getByRole("button", { name: "Stop automation" });
    const input = screen.getByLabelText("Type STOP to confirm");
    expect(confirm).toBeDisabled();

    await user.type(input, "stop");
    expect(confirm).toBeDisabled();

    await user.clear(input);
    await user.type(input, "STOP");
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = mock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(post).toBeDefined();
    expect(String((post?.[1] as RequestInit).body)).toBe('{"confirm":"STOP"}');
  });

  it("offers resume instead of stop while automation is stopped", async () => {
    const { mock } = routeFetch({ "/tasks/emergency-resume": () => ({ stopped: false }) });
    const user = userEvent.setup();
    renderWithProviders(<EmergencyStopControl stopped />, { route: "/automation" });

    expect(screen.queryByRole("button", { name: "Emergency stop" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Resume automation" }));
    await waitFor(() => expect(mock.mock.calls.some(([url]) => String(url).includes("/tasks/emergency-resume"))).toBe(true));
  });
});
