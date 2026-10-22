import { useState } from "react";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Tabs } from "../../components/ui/Tabs";
import { LogsPanel } from "./LogsPanel";
import { TracePanel } from "./TracePanel";
import { useConfigCheckQuery, useVersionQuery, type ConfigCheck } from "./developerQueries";

type DeveloperTab = "version" | "logs" | "config" | "trace";

const TAB_ITEMS: Array<{ id: DeveloperTab; label: string }> = [
  { id: "version", label: "Version" },
  { id: "logs", label: "Logs" },
  { id: "config", label: "Config checks" },
  { id: "trace", label: "Task trace" },
];

const CHECK_TONE: Record<ConfigCheck["status"], BadgeTone> = { ok: "success", warning: "warning", error: "danger" };

function VersionPanel() {
  const version = useVersionQuery();
  if (version.isPending) return <SkeletonList rows={3} label="Loading version" />;
  if (version.isError) return <ErrorState message={version.error.message} onRetry={() => void version.refetch()} />;
  const info = version.data;
  if (!info) return null;
  return (
    <dl className="grid gap-4 sm:grid-cols-2">
      <div>
        <dt className="text-xs text-muted">App version</dt>
        <dd className="text-sm text-ink">{info.app_version}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted">Python</dt>
        <dd className="text-sm text-ink">{info.python_version}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted">Environment</dt>
        <dd className="text-sm text-ink">{info.environment}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted">Agent protocol</dt>
        <dd className="text-sm text-ink">{info.protocol_version}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted">Background workers</dt>
        <dd className="text-sm text-ink">{info.background_workers ? "Running" : "Not running in this process"}</dd>
      </div>
    </dl>
  );
}

function ConfigPanel() {
  const checks = useConfigCheckQuery();
  if (checks.isPending) return <SkeletonList rows={4} label="Running configuration checks" />;
  if (checks.isError) return <ErrorState message={checks.error.message} onRetry={() => void checks.refetch()} />;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">Shows whether each setting is present and well formed. Values are never shown.</p>
      <ul aria-label="Configuration checks" className="flex flex-col gap-2">
        {(checks.data ?? []).map((check) => (
          <li key={check.key} className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-3 text-sm">
            <Badge tone={CHECK_TONE[check.status] ?? "neutral"}>{check.status}</Badge>
            <code className="text-ink">{check.key}</code>
            <span className="text-muted">{check.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Developer console: version, redacted logs, configuration checks and a task trace lookup. */
export default function DeveloperPage() {
  const [tab, setTab] = useState<DeveloperTab>("version");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Developer" description="Diagnostics for this installation. Secrets are never shown; the API redacts them before they reach this page." />
      <Tabs label="Developer tools" value={tab} onChange={(id) => setTab(id as DeveloperTab)} items={TAB_ITEMS} />
      <Card role="tabpanel" aria-labelledby={`tab-${tab}`} className="flex flex-col gap-4">
        <CardTitle>{TAB_ITEMS.find((item) => item.id === tab)?.label}</CardTitle>
        {tab === "version" ? <VersionPanel /> : null}
        {tab === "logs" ? <LogsPanel /> : null}
        {tab === "config" ? <ConfigPanel /> : null}
        {tab === "trace" ? <TracePanel /> : null}
      </Card>
    </div>
  );
}
