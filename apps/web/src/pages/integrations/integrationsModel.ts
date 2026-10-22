/**
 * Labels, action rules and the callback banner for the Integrations page. Pure, so each rule can be
 * tested. Reason codes come from docs/phase2/integrations.md. Provider text is never shown: the
 * callback redirect carries only fixed codes, and every message here is written in this file.
 */

import type { BadgeTone } from "../../components/ui/Badge";

export type IntegrationStatus =
  | "planned"
  | "requires_configuration"
  | "not_connected"
  | "connected"
  | "expired"
  | "error"
  | "disconnected";

export interface IntegrationItem {
  id: string;
  name: string;
  category: string;
  status: IntegrationStatus;
  implemented: boolean;
  requires_configuration: string[];
  scopes_requested: string[];
  /** Scopes the provider granted. Empty unless a connection exists. */
  permissions: string[];
  docs: string;
  account_label: string | null;
  last_checked_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
}

export const INTEGRATION_STATUS_LABEL: Record<IntegrationStatus, string> = {
  planned: "Planned",
  requires_configuration: "Requires configuration",
  not_connected: "Not connected",
  connected: "Connected",
  expired: "Expired",
  error: "Error",
  disconnected: "Disconnected",
};

export const INTEGRATION_STATUS_TONE: Record<IntegrationStatus, BadgeTone> = {
  planned: "neutral",
  requires_configuration: "warning",
  not_connected: "neutral",
  connected: "success",
  expired: "warning",
  error: "danger",
  disconnected: "neutral",
};

/**
 * The docs value is a repository path such as docs/integrations/google_calendar.md. It becomes a link only
 * when it is an absolute or site-relative URL. Repository paths are shown as text, because the web
 * app does not serve them.
 */
export function docsHref(docs: string): string | null {
  if (/^https?:\/\//.test(docs) || docs.startsWith("/")) return docs;
  return null;
}

/** Readable text for each reason code: the callback reasons and the codes stored on a connection. */
const REASON_MESSAGES: Record<string, string> = {
  not_signed_in: "Your JARVIS session ended before the sign-in finished. Sign in again, then connect.",
  invalid_state: "The sign-in request was not recognised. Start the connection again.",
  state_expired: "The sign-in took too long and expired. Start the connection again.",
  state_user_mismatch: "That sign-in belongs to a different JARVIS account. Start again while signed in to this account.",
  provider_denied: "Access was declined, so nothing was connected.",
  invalid_callback: "The provider's reply was incomplete. Start the connection again.",
  missing_scope: "A permission JARVIS needs was not granted, so nothing was connected. Connect again and allow every permission.",
  exchange_failed: "The provider did not accept the sign-in. Try connecting again.",
  provider_unavailable: "The provider could not be reached. Try again later.",
  provider_error: "The provider returned an error. Try again later.",
  not_configured: "This integration is not set up on the server yet.",
  rate_limited: "Too many attempts in a short time. Wait a minute, then try again.",
  unknown_integration: "That integration does not exist.",
  not_connected: "This account is not connected. Connect it first.",
  reconnect_required: "The connection has expired or was revoked. Connect again.",
  credential_unreadable: "The stored connection cannot be read. Connect again.",
};

export const GENERIC_CONNECTION_MESSAGE = "The connection did not finish. Try again.";

/** The message for a reason code. Unknown codes get the generic message. */
export function reasonMessage(reason: string | null | undefined): string {
  if (!reason) return GENERIC_CONNECTION_MESSAGE;
  return Object.prototype.hasOwnProperty.call(REASON_MESSAGES, reason) ? REASON_MESSAGES[reason] : GENERIC_CONNECTION_MESSAGE;
}

export interface CallbackResult {
  provider: string | null;
  status: string;
  reason: string | null;
}

/** Reads the result that the OAuth callback left in the query string. Null when there is no result. */
export function readCallbackParams(params: URLSearchParams): CallbackResult | null {
  const status = params.get("status");
  if (!status) return null;
  return { provider: params.get("provider"), status, reason: params.get("reason") };
}

export interface Banner {
  tone: "success" | "danger" | "warning";
  title: string;
  description: string;
}

/** The catalogue name for a provider id, or null. The query string is untrusted, so only known ids are named. */
export function providerName(items: readonly IntegrationItem[], provider: string | null): string | null {
  if (!provider) return null;
  return items.find((item) => item.id === provider)?.name ?? null;
}

/** The banner shown after a callback. Only catalogue names and the fixed reason texts are rendered. */
export function callbackBanner(result: CallbackResult, items: readonly IntegrationItem[]): Banner {
  const name = providerName(items, result.provider) ?? "The integration";
  if (result.status === "connected") {
    return {
      tone: "success",
      title: `${name} is connected.`,
      description: "Use Test connection to check that the account still works.",
    };
  }
  if (result.status === "error") {
    return {
      tone: "danger",
      title: `${name} was not connected.`,
      description: reasonMessage(result.reason),
    };
  }
  return { tone: "warning", title: "The sign-in did not finish.", description: GENERIC_CONNECTION_MESSAGE };
}

/** The browser only leaves the app for an https address. The URL itself always comes from the server. */
export function isSafeAuthorizationUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

export interface IntegrationActions {
  connect: boolean;
  reconnect: boolean;
  test: boolean;
  testLabel: string;
  disconnect: boolean;
}

const NO_ACTIONS: IntegrationActions = { connect: false, reconnect: false, test: false, testLabel: "Test connection", disconnect: false };

/** Which buttons a card shows. Planned integrations and those missing configuration have none. */
export function integrationActions(item: IntegrationItem): IntegrationActions {
  if (!item.implemented) return NO_ACTIONS;
  switch (item.status) {
    case "not_connected":
    case "disconnected":
      return { ...NO_ACTIONS, connect: true };
    case "connected":
      return { ...NO_ACTIONS, test: true, disconnect: true };
    case "error":
      return { ...NO_ACTIONS, test: true, testLabel: "Test again", disconnect: true };
    case "expired":
      return { ...NO_ACTIONS, reconnect: true, disconnect: true };
    case "planned":
    case "requires_configuration":
      return NO_ACTIONS;
  }
}

/** The word the user types to confirm a disconnect. The API does not check it. */
export const DISCONNECT_PHRASE = "DISCONNECT";

export function isDisconnectPhraseMatch(input: string): boolean {
  return input === DISCONNECT_PHRASE;
}

/** Text for a failed request. The server's envelope message is fixed text, so it is safe to show. */
export function errorText(cause: unknown): string {
  return cause instanceof Error && cause.message ? cause.message : "Try again.";
}

/**
 * What disconnecting does, written for any provider. Whether the provider also revoked the grant is
 * decided by the server, so the message shown after disconnecting carries that answer.
 */
export function disconnectDescription(item: IntegrationItem): string {
  return `JARVIS deletes the token it stores for ${item.name} on this server. The message after disconnecting says whether ${item.name} also revoked the access, and what to do if it did not.`;
}
