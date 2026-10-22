import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useProvidersQuery, useSettingsQuery } from "../../services/queries";
import { AssistantRoutingPanel } from "./AssistantRoutingPanel";
import { ProviderCard } from "./ProviderCard";
import { UsagePanel } from "./UsagePanel";

/** AI providers: keys, connection tests, models, timeouts, routing, usage and diagnostics. */
export default function AIModelsPage() {
  const providers = useProvidersQuery();
  const settings = useSettingsQuery();

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="AI models" description="Choose the provider that answers, add its key, and check that it works. Keys are write-only: JARVIS shows only a short hint." />

      {providers.isPending || settings.isPending ? (
        <SkeletonList rows={3} label="Loading providers" />
      ) : providers.isError ? (
        <ErrorState message={providers.error.message} onRetry={() => void providers.refetch()} />
      ) : settings.isError ? (
        <ErrorState message={settings.error.message} onRetry={() => void settings.refetch()} />
      ) : providers.data.length === 0 ? (
        <EmptyState title="No providers are available" description="This server reports no AI providers. Check the server configuration." />
      ) : (
        <>
          <AssistantRoutingPanel key={settings.data.ai.active_provider + String(settings.data.ai.fallback_provider)} settings={settings.data} providers={providers.data} />
          <div className="grid gap-6 xl:grid-cols-2">
            {providers.data.map((provider) => (
              <ProviderCard key={provider.id} provider={provider} />
            ))}
          </div>
        </>
      )}

      <UsagePanel />
    </div>
  );
}
