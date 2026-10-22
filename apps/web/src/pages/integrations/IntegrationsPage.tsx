import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { IntegrationCard, type TestOutcome } from "./IntegrationCard";
import { DisconnectDialog } from "./IntegrationDialogs";
import {
  callbackBanner,
  errorText,
  isSafeAuthorizationUrl,
  readCallbackParams,
  type Banner,
  type CallbackResult,
  type IntegrationItem,
} from "./integrationsModel";
import { useConnectIntegration, useIntegrationsQuery, useTestIntegration } from "./integrationsQueries";
import { assignLocation } from "./navigation";

/**
 * Connected services. Connect sends the browser to the provider's sign-in page, and the provider
 * returns to /integrations with a result in the query string. That result is shown once, then removed
 * from the address bar. Planned services are listed with no actions.
 */
export default function IntegrationsPage() {
  const integrations = useIntegrationsQuery();
  const connect = useConnectIntegration();
  const test = useTestIntegration();
  const { toast } = useToast();
  const [params] = useSearchParams();
  const [callback, setCallback] = useState<CallbackResult | null>(() => readCallbackParams(params));
  const [redirectingId, setRedirectingId] = useState<string | null>(null);
  const [testOutcomes, setTestOutcomes] = useState<Record<string, TestOutcome>>({});
  const [disconnectTarget, setDisconnectTarget] = useState<IntegrationItem | null>(null);

  const items = integrations.data ?? [];
  const busy = redirectingId !== null || test.isPending;
  const banner: Banner | null = callback ? callbackBanner(callback, items) : null;

  // The result has been read into state, so the query string is removed. The history state is kept
  // so that the router's own navigation state is not disturbed.
  useEffect(() => {
    if (window.location.search) {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.hash}`);
    }
  }, []);

  const startConnect = (item: IntegrationItem): void => {
    setRedirectingId(item.id);
    connect.mutate(item.id, {
      onSuccess: (result) => {
        if (!isSafeAuthorizationUrl(result.authorization_url)) {
          setRedirectingId(null);
          toast({ tone: "error", title: "Sign-in could not start", description: "The server sent an address that JARVIS does not open." });
          return;
        }
        assignLocation(result.authorization_url);
      },
      onError: (cause) => {
        setRedirectingId(null);
        toast({ tone: "error", title: "Sign-in could not start", description: errorText(cause) });
        void integrations.refetch();
      },
    });
  };

  const runTest = (item: IntegrationItem): void => {
    test.mutate(item.id, {
      onSuccess: (result) => setTestOutcomes((current) => ({ ...current, [item.id]: { ok: result.ok, message: result.message } })),
      onError: (cause) => setTestOutcomes((current) => ({ ...current, [item.id]: { ok: false, message: errorText(cause) } })),
    });
  };

  const onDisconnected = (item: IntegrationItem): void => {
    setDisconnectTarget(null);
    setTestOutcomes((current) => {
      const next = { ...current };
      delete next[item.id];
      return next;
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Integrations"
        description="Connect outside services with your own accounts. Each service asks only for the access listed on its card."
      />

      {callback && banner ? <ResultBanner banner={banner} onDismiss={() => setCallback(null)} /> : null}

      <Card>
        <p className="text-sm text-ink">
          Sign-in happens on the provider&apos;s own page, so JARVIS never sees your password. You can disconnect at any time. Services marked
          Planned have no actions yet.
        </p>
      </Card>

      {integrations.isPending ? (
        <SkeletonList rows={3} label="Loading integrations" />
      ) : integrations.isError ? (
        <ErrorState message={integrations.error.message} onRetry={() => void integrations.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title="No integrations listed" description="The catalogue is empty." />
      ) : (
        <ul aria-label="Integrations" className="grid gap-4 md:grid-cols-2">
          {items.map((item) => (
            <li key={item.id}>
              <IntegrationCard
                item={item}
                busy={busy}
                redirecting={redirectingId === item.id}
                testing={test.isPending && test.variables === item.id}
                testOutcome={testOutcomes[item.id] ?? null}
                onConnect={() => startConnect(item)}
                onTest={() => runTest(item)}
                onDisconnect={() => setDisconnectTarget(item)}
              />
            </li>
          ))}
        </ul>
      )}

      <DisconnectDialog item={disconnectTarget} onClose={() => setDisconnectTarget(null)} onDisconnected={onDisconnected} />
    </div>
  );
}

function ResultBanner({ banner, onDismiss }: { banner: Banner; onDismiss: () => void }) {
  const tone =
    banner.tone === "success"
      ? "border-success/50 bg-success/10"
      : banner.tone === "danger"
        ? "border-danger/50 bg-danger/10"
        : "border-warning/50 bg-warning/10";
  return (
    <div role={banner.tone === "danger" ? "alert" : "status"} className={`flex flex-wrap items-start justify-between gap-3 rounded-card border p-4 ${tone}`}>
      <div className="min-w-0">
        <p className="text-sm font-semibold text-ink">{banner.title}</p>
        <p className="text-sm text-ink">{banner.description}</p>
      </div>
      <Button size="sm" variant="secondary" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  );
}
