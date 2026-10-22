import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PrivacySection } from "../pages/settings/PrivacySection";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

function clearRoute(matched: number, bodies: unknown[]) {
  return {
    "/conversations/clear": (_url: URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as { dry_run: boolean; days: number };
      bodies.push(body);
      return { matched: body.dry_run ? matched : 0, deleted: body.dry_run ? 0 : matched, dry_run: body.dry_run };
    },
  };
}

describe("clear old conversations", () => {
  it("counts first and keeps the delete button disabled until DELETE is typed", async () => {
    const bodies: unknown[] = [];
    routeFetch(clearRoute(2, bodies));
    renderWithProviders(<PrivacySection privacy={{ store_conversations: true }} />);
    const user = userEvent.setup();

    const days = screen.getByLabelText("Older than (days)");
    await user.clear(days);
    await user.type(days, "30");
    await user.click(screen.getByRole("button", { name: "Count them" }));

    expect(await screen.findByText("2 conversations are older than 30 days and not pinned.")).toBeInTheDocument();
    expect(bodies).toEqual([{ days: 30, dry_run: true }]);
    const deleteButton = screen.getByRole("button", { name: "Delete 2 conversations" });
    expect(deleteButton).toBeDisabled();

    await user.type(screen.getByLabelText("Type DELETE to confirm"), "delete");
    expect(deleteButton).toBeDisabled();
  });

  it("deletes after the confirmation word, and sends the real request", async () => {
    const bodies: unknown[] = [];
    routeFetch(clearRoute(2, bodies));
    renderWithProviders(<PrivacySection privacy={{ store_conversations: true }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Count them" }));
    await screen.findByRole("button", { name: "Delete 2 conversations" });
    await user.type(screen.getByLabelText("Type DELETE to confirm"), "DELETE");
    await user.click(screen.getByRole("button", { name: "Delete 2 conversations" }));

    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({ days: 90, dry_run: false });
    expect(await screen.findByText("Old conversations deleted")).toBeInTheDocument();
  });

  it("offers no delete when nothing is old enough", async () => {
    routeFetch(clearRoute(0, []));
    renderWithProviders(<PrivacySection privacy={{ store_conversations: true }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Count them" }));

    expect(await screen.findByText("No conversations are older than 90 days.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
  });
});
