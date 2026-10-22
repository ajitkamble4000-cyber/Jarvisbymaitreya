import { useId } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { formatDateTime, humanize } from "../../services/format";
import {
  INTEGRATION_STATUS_LABEL,
  INTEGRATION_STATUS_TONE,
  docsHref,
  integrationActions,
  reasonMessage,
  type IntegrationItem,
} from "./integrationsModel";

export interface TestOutcome {
  ok: boolean;
  message: string;
}

export interface IntegrationCardProps {
  item: IntegrationItem;
  /** Another action on the page is running, so every action here waits. */
  busy: boolean;
  /** The sign-in address for this integration is known and the browser is leaving for it. */
  redirecting: boolean;
  testing: boolean;
  testOutcome: TestOutcome | null;
  onConnect: () => void;
  onTest: () => void;
  onDisconnect: () => void;
}

/** One integration. Shows its status as text, what it needs or grants, and only the actions its status allows. */
export function IntegrationCard({ item, busy, redirecting, testing, testOutcome, onConnect, onTest, onDisconnect }: IntegrationCardProps) {
  const titleId = useId();
  const actions = integrationActions(item);
  const href = docsHref(item.docs);
  const grantedList = item.permissions.length > 0;
  const listed = grantedList ? item.permissions : item.scopes_requested;
  const needsConfiguration = item.status === "requires_configuration";
  const hasActions = actions.connect || actions.reconnect || actions.test || actions.disconnect;

  return (
    <section aria-labelledby={titleId} className="h-full">
      <Card className="flex h-full flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle id={titleId}>{item.name}</CardTitle>
            <p className="text-xs text-muted">{humanize(item.category)}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge tone={INTEGRATION_STATUS_TONE[item.status]}>{INTEGRATION_STATUS_LABEL[item.status]}</Badge>
            {item.implemented ? null : <Badge tone="warning">Not implemented yet</Badge>}
          </div>
        </div>

        <StatusDetail item={item} />

        {needsConfiguration && item.requires_configuration.length > 0 ? (
          <div>
            <h3 className="text-xs font-medium text-ink">Server settings it needs</h3>
            <ul className="mt-1 flex flex-wrap gap-1">
              {item.requires_configuration.map((name) => (
                <li key={name}>
                  <code className="rounded bg-raised px-1.5 py-0.5 text-xs text-ink">{name}</code>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {listed.length > 0 ? (
          <div>
            <h3 className="text-xs font-medium text-ink">{grantedList ? "Granted permissions" : "Access it asks for"}</h3>
            <ul aria-label={grantedList ? `Granted permissions for ${item.name}` : `Access requested for ${item.name}`} className="mt-1 flex flex-col gap-1 text-xs text-muted">
              {listed.map((scope) => (
                <li key={scope} className="break-all">
                  {scope}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {item.account_label ? (
          <p className="text-xs text-muted">
            Account: <span className="text-ink">{item.account_label}</span>
          </p>
        ) : null}
        {item.last_checked_at ? <p className="text-xs text-muted">Last checked {formatDateTime(item.last_checked_at)}</p> : null}

        {redirecting ? (
          <p role="status" className="text-sm text-ink">
            Opening {item.name} sign-in. Finish there, then you will return to JARVIS.
          </p>
        ) : null}

        {testOutcome ? (
          <p role={testOutcome.ok ? "status" : "alert"} className={testOutcome.ok ? "text-sm text-success" : "text-sm text-danger"}>
            {testOutcome.message}
          </p>
        ) : null}

        {hasActions ? (
          <div className="mt-auto flex flex-wrap gap-2">
            {actions.connect ? (
              <Button variant="primary" loading={redirecting} disabled={busy} onClick={onConnect}>
                Connect
              </Button>
            ) : null}
            {actions.reconnect ? (
              <Button variant="primary" loading={redirecting} disabled={busy} onClick={onConnect}>
                Reconnect
              </Button>
            ) : null}
            {actions.test ? (
              <Button variant="secondary" loading={testing} disabled={busy} onClick={onTest}>
                {actions.testLabel}
              </Button>
            ) : null}
            {actions.disconnect ? (
              <Button variant="danger" disabled={busy} onClick={onDisconnect}>
                Disconnect
              </Button>
            ) : null}
          </div>
        ) : null}

        <p className={hasActions ? "text-xs text-muted" : "mt-auto text-xs text-muted"}>
          Documentation:{" "}
          {href ? (
            <a href={href} className="text-accent underline underline-offset-4">
              {item.docs}
            </a>
          ) : (
            <code className="text-ink">{item.docs}</code>
          )}
        </p>
      </Card>
    </section>
  );
}

/** The sentence under the status badge. Each status says what the user can do next, in words. */
function StatusDetail({ item }: { item: IntegrationItem }) {
  switch (item.status) {
    case "requires_configuration":
      return <p className="text-sm text-ink">The server is missing settings for this integration, so it cannot connect yet.</p>;
    case "not_connected":
      return item.implemented ? <p className="text-sm text-muted">Connect to let JARVIS use this account.</p> : null;
    case "disconnected":
      return <p className="text-sm text-muted">Disconnected. Connect again to use this account.</p>;
    case "connected":
      return <p className="text-sm text-muted">JARVIS can use this account. Test the connection at any time.</p>;
    case "expired":
      return <p className="text-sm text-warning">The sign-in has expired. Reconnect to continue.</p>;
    case "error":
      return (
        <p className="text-sm text-danger">
          {item.last_error_code ? reasonMessage(item.last_error_code) : "The last check failed. Test again to see whether it is fixed."}
        </p>
      );
    case "planned":
      return <p className="text-sm text-muted">Planned. There are no actions for this service yet.</p>;
  }
}
