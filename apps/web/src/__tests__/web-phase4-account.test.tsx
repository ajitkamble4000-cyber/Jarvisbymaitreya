import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ACCOUNT_EXPORT_FILE, PrivacySection } from "../pages/settings/PrivacySection";
import { renderWithProviders } from "../test/utils";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("download my data", () => {
  it("fetches the account export and saves it under the server's file name", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Disposition": 'attachment; filename="jarvis-account-export.json"' } })),
    );
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:account", revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWithProviders(<PrivacySection privacy={{ store_conversations: true }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Download my data" }));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/v1/account/export");
    expect(ACCOUNT_EXPORT_FILE).toBe("jarvis-account-export.json");
  });

  it("says when the export fails, in words", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "not_authenticated", message: "Sign in to continue." } }), { status: 401 })),
    );
    renderWithProviders(<PrivacySection privacy={{ store_conversations: true }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Download my data" }));

    expect(await screen.findByText("Your data was not exported")).toBeInTheDocument();
  });
});
