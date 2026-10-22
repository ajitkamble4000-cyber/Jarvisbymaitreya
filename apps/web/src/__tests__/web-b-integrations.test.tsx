import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import IntegrationsPage from "../pages/integrations/IntegrationsPage";
import { assignLocation } from "../pages/integrations/navigation";
import {
  DISCONNECT_PHRASE,
  callbackBanner,
  disconnectDescription,
  docsHref,
  integrationActions,
  isDisconnectPhraseMatch,
  isSafeAuthorizationUrl,
  readCallbackParams,
  reasonMessage,
  type IntegrationItem,
} from "../pages/integrations/integrationsModel";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

vi.mock("../pages/integrations/navigation", () => ({ assignLocation: vi.fn() }));

const base: Omit<IntegrationItem, "id" | "name" | "status"> = {
  category: "productivity",
  implemented: true,
  requires_configuration: [],
  scopes_requested: [],
  permissions: [],
  docs: "docs/integrations/google_calendar.md",
  account_label: null,
  last_checked_at: null,
  last_error_code: null,
  last_error_message: null,
};

const googleConnected: IntegrationItem = {
  ...base,
  id: "google_calendar",
  name: "Google Calendar",
  status: "connected",
  requires_configuration: ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"],
  scopes_requested: ["openid", "email", "https://www.googleapis.com/auth/calendar.readonly"],
  permissions: ["openid", "email", "https://www.googleapis.com/auth/calendar.readonly"],
  account_label: "ada@example.com",
  last_checked_at: "2026-10-20T10:00:00+00:00",
};

const githubNotConnected: IntegrationItem = {
  ...base,
  id: "github",
  name: "GitHub",
  category: "development",
  status: "not_connected",
  docs: "docs/integrations/github.md",
  scopes_requested: ["read:user", "public_repo"],
};

const githubConnected: IntegrationItem = {
  ...githubNotConnected,
  status: "connected",
  permissions: ["read:user", "public_repo"],
  account_label: "ada",
};

const slackPlanned: IntegrationItem = {
  ...base,
  id: "slack",
  name: "Slack",
  category: "communication",
  implemented: false,
  status: "planned",
  docs: "docs/integrations/slack.md",
  scopes_requested: ["chat:write"],
};

describe("Integrations page: catalogue", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows each status as text, the granted permissions and the account, and only the actions it allows", async () => {
    const { calls } = routeFetch({ "/integrations": () => ({ items: [googleConnected, githubNotConnected, slackPlanned] }) });
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const google = await screen.findByRole("region", { name: "Google Calendar" });
    expect(within(google).getByText("Connected")).toBeInTheDocument();
    expect(within(google).getByText("ada@example.com")).toBeInTheDocument();
    expect(within(google).getByRole("list", { name: "Granted permissions for Google Calendar" })).toBeInTheDocument();
    expect(within(google).getByText("https://www.googleapis.com/auth/calendar.readonly")).toBeInTheDocument();
    expect(within(google).getByRole("button", { name: "Test connection" })).toBeInTheDocument();
    expect(within(google).getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(within(google).queryByRole("button", { name: "Connect" })).toBeNull();

    const github = screen.getByRole("region", { name: "GitHub" });
    expect(within(github).getByText("Not connected")).toBeInTheDocument();
    expect(within(github).getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(within(github).queryByRole("button", { name: "Disconnect" })).toBeNull();

    const slack = screen.getByRole("region", { name: "Slack" });
    expect(within(slack).getByText("Not implemented yet")).toBeInTheDocument();
    expect(within(slack).queryAllByRole("button")).toHaveLength(0);

    expect(calls).toEqual(["/integrations"]);
  });

  it("lists the server settings a service needs and offers no connect button while they are missing", async () => {
    const unconfigured: IntegrationItem = { ...googleConnected, status: "requires_configuration", permissions: [], account_label: null };
    routeFetch({ "/integrations": () => ({ items: [unconfigured] }) });
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const card = await screen.findByRole("region", { name: "Google Calendar" });
    expect(within(card).getByText("Requires configuration")).toBeInTheDocument();
    expect(within(card).getByText("GOOGLE_OAUTH_CLIENT_ID")).toBeInTheDocument();
    expect(within(card).getByText("GOOGLE_OAUTH_CLIENT_SECRET")).toBeInTheDocument();
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
  });

  it("shows Reconnect for an expired connection and Test again for an error", async () => {
    const expired: IntegrationItem = { ...googleConnected, status: "expired" };
    const errored: IntegrationItem = { ...githubConnected, status: "error", last_error_code: "provider_unavailable" };
    routeFetch({ "/integrations": () => ({ items: [expired, errored] }) });
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const google = await screen.findByRole("region", { name: "Google Calendar" });
    expect(within(google).getByText("Expired")).toBeInTheDocument();
    expect(within(google).getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    expect(within(google).queryByRole("button", { name: "Test connection" })).toBeNull();

    const github = screen.getByRole("region", { name: "GitHub" });
    expect(within(github).getByRole("button", { name: "Test again" })).toBeInTheDocument();
    expect(within(github).getByText(reasonMessage("provider_unavailable"))).toBeInTheDocument();
  });

  it("derives the actions from the status alone", () => {
    expect(integrationActions(slackPlanned)).toMatchObject({ connect: false, test: false, disconnect: false, reconnect: false });
    expect(integrationActions({ ...googleConnected, status: "requires_configuration" })).toMatchObject({ connect: false });
    expect(integrationActions(githubNotConnected)).toMatchObject({ connect: true, disconnect: false });
    expect(integrationActions({ ...githubNotConnected, status: "disconnected" })).toMatchObject({ connect: true });
    expect(integrationActions({ ...googleConnected, status: "expired" })).toMatchObject({ reconnect: true, disconnect: true, test: false });
    expect(integrationActions({ ...githubConnected, status: "error" })).toMatchObject({ test: true, testLabel: "Test again" });
  });

  it("links only to documentation that the web app serves", () => {
    expect(docsHref("docs/integrations/google_calendar.md")).toBeNull();
    expect(docsHref("https://example.com/docs")).toBe("https://example.com/docs");
    expect(docsHref("/help")).toBe("/help");
  });
});

describe("Integrations page: connect", () => {
  beforeEach(() => {
    vi.mocked(assignLocation).mockReset();
  });

  it("asks the server for the sign-in address, then sends the browser to it", async () => {
    const authorizationUrl = "https://github.com/login/oauth/authorize?client_id=abc&state=xyz";
    const { calls, mock } = routeFetch({
      "/integrations": () => ({ items: [githubNotConnected] }),
      "/integrations/github/connect": () => ({ authorization_url: authorizationUrl }),
    });
    const user = userEvent.setup();
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const github = await screen.findByRole("region", { name: "GitHub" });
    await user.click(within(github).getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(assignLocation).toHaveBeenCalledWith(authorizationUrl));
    expect(calls).toContain("/integrations/github/connect");
    const post = mock.mock.calls.find(([path]) => String(path).endsWith("/integrations/github/connect"));
    expect((post?.[1] as RequestInit | undefined)?.method).toBe("POST");
    expect(screen.getByText(/Opening GitHub sign-in/)).toBeInTheDocument();
  });

  it("refuses an address that is not https and does not leave the app", async () => {
    routeFetch({
      "/integrations": () => ({ items: [githubNotConnected] }),
      "/integrations/github/connect": () => ({ authorization_url: "javascript:alert(1)" }),
    });
    const user = userEvent.setup();
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const github = await screen.findByRole("region", { name: "GitHub" });
    await user.click(within(github).getByRole("button", { name: "Connect" }));

    expect(await screen.findByText("Sign-in could not start")).toBeInTheDocument();
    expect(assignLocation).not.toHaveBeenCalled();
    expect(isSafeAuthorizationUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeAuthorizationUrl("https://accounts.example.test/auth")).toBe(true);
  });
});

describe("Integrations page: result of the callback", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("shows a success banner, then removes the result from the address bar", async () => {
    window.history.replaceState(null, "", "/integrations?provider=github&status=connected");
    const replace = vi.spyOn(window.history, "replaceState");
    routeFetch({ "/integrations": () => ({ items: [githubConnected] }) });
    renderWithProviders(<IntegrationsPage />, { route: "/integrations?provider=github&status=connected" });

    expect(await screen.findByText("GitHub is connected.")).toBeInTheDocument();
    expect(screen.getByText("Use Test connection to check that the account still works.")).toBeInTheDocument();
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(replace.mock.calls.some(([, , url]) => url === "/integrations")).toBe(true);
    replace.mockRestore();
  });

  it("shows the readable message for the reason code, and not the raw code", async () => {
    window.history.replaceState(null, "", "/integrations?provider=github&status=error&reason=state_expired");
    routeFetch({ "/integrations": () => ({ items: [githubNotConnected] }) });
    renderWithProviders(<IntegrationsPage />, { route: "/integrations?provider=github&status=error&reason=state_expired" });

    // The banner names the provider from the catalogue, so wait for the catalogue first.
    await screen.findByRole("region", { name: "GitHub" });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("GitHub was not connected.");
    expect(alert).toHaveTextContent("The sign-in took too long and expired. Start the connection again.");
    expect(alert).not.toHaveTextContent("state_expired");
  });

  it("uses the generic message for an unknown reason, and names only catalogue providers", async () => {
    window.history.replaceState(null, "", "/integrations?provider=evil&status=error&reason=something_new");
    routeFetch({ "/integrations": () => ({ items: [githubNotConnected] }) });
    renderWithProviders(<IntegrationsPage />, { route: "/integrations?provider=evil&status=error&reason=something_new" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The integration was not connected.");
    expect(alert).toHaveTextContent("The connection did not finish. Try again.");
    expect(alert).not.toHaveTextContent("something_new");
    expect(alert).not.toHaveTextContent("evil");
  });

  it("maps the callback query and reason codes to fixed text", () => {
    expect(readCallbackParams(new URLSearchParams(""))).toBeNull();
    expect(readCallbackParams(new URLSearchParams("provider=github&status=error&reason=provider_denied"))).toEqual({
      provider: "github",
      status: "error",
      reason: "provider_denied",
    });
    expect(reasonMessage("provider_denied")).toBe("Access was declined, so nothing was connected.");
    expect(reasonMessage("definitely_not_a_code")).toBe("The connection did not finish. Try again.");
    expect(reasonMessage(null)).toBe("The connection did not finish. Try again.");
    expect(callbackBanner({ provider: "google_calendar", status: "error", reason: "not_configured" }, [googleConnected])).toEqual({
      tone: "danger",
      title: "Google Calendar was not connected.",
      description: "This integration is not set up on the server yet.",
    });
  });
});

describe("Integrations page: test and disconnect", () => {
  it("runs the provider check and shows its message, then reloads the catalogue", async () => {
    const { calls } = routeFetch({
      "/integrations": () => ({ items: [googleConnected] }),
      "/integrations/google_calendar/test": () => ({
        ok: true,
        status: "connected",
        account_label: "ada@example.com",
        message: "The provider accepted the connection.",
      }),
    });
    const user = userEvent.setup();
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const card = await screen.findByRole("region", { name: "Google Calendar" });
    await user.click(within(card).getByRole("button", { name: "Test connection" }));

    expect(await within(card).findByText("The provider accepted the connection.")).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((call) => call === "/integrations")).toHaveLength(2));
  });

  it("shows a failed check as an alert", async () => {
    routeFetch({
      "/integrations": () => ({ items: [githubConnected] }),
      "/integrations/github/test": () => ({
        ok: false,
        status: "expired",
        account_label: "ada",
        message: "The connection has expired or was revoked. Connect again.",
      }),
    });
    const user = userEvent.setup();
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const card = await screen.findByRole("region", { name: "GitHub" });
    await user.click(within(card).getByRole("button", { name: "Test connection" }));

    expect(await within(card).findByRole("alert")).toHaveTextContent("The connection has expired or was revoked. Connect again.");
  });

  it("requires the typed word before Disconnect is enabled, and says GitHub keeps its grant", async () => {
    const { calls, mock } = routeFetch({
      "/integrations": () => ({ items: [githubConnected] }),
      "/integrations/github/connection": () => ({
        status: "disconnected",
        provider_revoked: false,
        message: "The stored token was deleted. The grant stays active at GitHub until you remove JARVIS under GitHub Settings > Applications.",
      }),
    });
    const user = userEvent.setup();
    renderWithProviders(<IntegrationsPage />, { route: "/integrations" });

    const card = await screen.findByRole("region", { name: "GitHub" });
    await user.click(within(card).getByRole("button", { name: "Disconnect" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/deletes the token it stores for GitHub on this server/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Disconnect GitHub" });
    expect(confirm).toBeDisabled();

    const input = within(dialog).getByLabelText(`Type ${DISCONNECT_PHRASE} to confirm`);
    await user.type(input, "disconnect");
    expect(confirm).toBeDisabled();
    await user.clear(input);
    await user.type(input, DISCONNECT_PHRASE);
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const del = mock.mock.calls.find(
      ([path, init]) => String(path).endsWith("/integrations/github/connection") && (init as RequestInit | undefined)?.method === "DELETE",
    );
    expect(del).toBeDefined();
    expect(calls).toContain("/integrations/github/connection");
  });

  it("describes a Google disconnect as a revoke at the provider", () => {
    expect(disconnectDescription(googleConnected)).toContain("JARVIS deletes the token it stores for Google Calendar on this server.");
    // The copy is the same for every provider. Whether access was revoked comes from the server's message.
    expect(disconnectDescription(githubConnected)).toContain("JARVIS deletes the token it stores for GitHub");
    expect(disconnectDescription(githubConnected)).not.toContain("GitHub Settings");
    expect(isDisconnectPhraseMatch("DISCONNECT")).toBe(true);
    expect(isDisconnectPhraseMatch("Disconnect")).toBe(false);
  });
});
