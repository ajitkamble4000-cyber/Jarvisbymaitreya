import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PairingDialog } from "../pages/devices/PairingDialog";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

const CODE = "KQ7M-2XBV";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open pairing
      </button>
      <PairingDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

describe("PairingDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the pairing code while open, and never shows it again after the dialog closes", async () => {
    const expiresAt = new Date(Date.now() + 5 * 60_000).toISOString();
    routeFetch({ "/devices/pairing-codes": () => ({ code: CODE, expires_at: expiresAt }) });
    const user = userEvent.setup();
    renderWithProviders(<Harness />, { route: "/devices" });

    await user.click(screen.getByRole("button", { name: "Open pairing" }));
    await user.click(await screen.findByRole("button", { name: "Create pairing code" }));

    expect(await screen.findByText(CODE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
    expect(screen.getByText(/Expires in/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.body.innerHTML).not.toContain(CODE);

    // Reopening starts from a clean state: the old code is not restored.
    await user.click(screen.getByRole("button", { name: "Open pairing" }));
    expect(await screen.findByRole("button", { name: "Create pairing code" })).toBeInTheDocument();
    expect(screen.queryByText(CODE)).toBeNull();
  });

  it("shows an expired code as expired and offers a new one", async () => {
    const expiresAt = new Date(Date.now() + 1000).toISOString();
    routeFetch({ "/devices/pairing-codes": () => ({ code: CODE, expires_at: expiresAt }) });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderWithProviders(<PairingDialog open onClose={() => undefined} />, { route: "/devices" });
      await user.click(await screen.findByRole("button", { name: "Create pairing code" }));
      expect(await screen.findByText(CODE)).toBeInTheDocument();
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      expect(await screen.findByText(/This code has expired/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
