import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActivityPage from "../pages/activity/ActivityPage";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Activity export", () => {
  it("downloads the audit CSV with no filter when none is set", async () => {
    const { mock } = routeFetch({
      "/audit": () => ({ items: [], total: 0 }),
      "/audit/export.csv": () => ({ ok: true }),
    });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:audit", revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWithProviders(<ActivityPage />, { route: "/activity" });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const exported = mock.mock.calls.map(([input]) => String(input)).find((url) => url.includes("/audit/export.csv"));
    expect(exported).toBe("/api/v1/audit/export.csv");
  });

  it("sends the outcome filter with the export", async () => {
    const { mock } = routeFetch({
      "/audit": () => ({ items: [], total: 0 }),
      "/audit/export.csv": () => ({ ok: true }),
    });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:audit", revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWithProviders(<ActivityPage />, { route: "/activity" });
    const user = userEvent.setup();

    const outcome = await screen.findByLabelText(/outcome/i);
    await user.selectOptions(outcome, "denied");
    await user.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const exported = mock.mock.calls.map(([input]) => String(input)).find((url) => url.includes("/audit/export.csv"));
    expect(exported).toBe("/api/v1/audit/export.csv?outcome=denied");
  });
});
