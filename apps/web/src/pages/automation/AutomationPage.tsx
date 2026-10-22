import { Link } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { humanize } from "../../services/format";
import { useDevicesQuery, useSettingsQuery, useToolsQuery } from "../../services/queries";
import type { ToolOut } from "../../types/api";
import { EmergencyStopControl } from "./EmergencyStopControl";

const RISK_TONE: Record<string, BadgeTone> = { low: "success", medium: "warning", high: "danger" };

function riskTone(level: string): BadgeTone {
  return RISK_TONE[level] ?? "neutral";
}

function ToolRow({ tool, enabled }: { tool: ToolOut; enabled: boolean }) {
  return (
    <li className="flex flex-col gap-1 rounded-lg border border-line bg-raised p-3 text-sm sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <span className="font-medium text-ink">{tool.title}</span>
        <span className="ml-2 text-xs text-muted">{humanize(tool.category)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={riskTone(tool.risk_level)}>Risk: {tool.risk_level}</Badge>
        <Badge tone={tool.approval_required ? "warning" : "neutral"}>{tool.approval_required ? "Needs approval" : "Approval not required"}</Badge>
        <Badge tone={enabled ? "success" : "neutral"}>{enabled ? "Enabled" : "Switched off"}</Badge>
      </div>
    </li>
  );
}

/**
 * Automation policy, read only apart from the emergency stop. It shows which tools are on, which
 * actions need approval and what each device allows. Changes are made in Settings.
 */
export default function AutomationPage() {
  const settings = useSettingsQuery();
  const tools = useToolsQuery();
  const devices = useDevicesQuery();
  const automation = settings.data?.automation;

  if (settings.isPending || tools.isPending) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Automation" />
        <SkeletonList rows={4} label="Loading automation policy" />
      </div>
    );
  }

  if (settings.isError || tools.isError || !automation) {
    const message = settings.error?.message ?? tools.error?.message ?? "The automation policy could not be loaded.";
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Automation" />
        <ErrorState message={message} onRetry={() => void Promise.all([settings.refetch(), tools.refetch()])} />
      </div>
    );
  }

  const toolList = tools.data ?? [];
  const disabled = new Set(automation.disabled_tools);
  const enabledCount = toolList.filter((tool) => !disabled.has(tool.name)).length;
  const deviceList = devices.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Automation"
        description="What JARVIS may do on its own, and what always waits for you. This page reads the settings; change them in Settings."
        actions={
          <Link to="/settings?tab=automation" className="text-sm text-accent underline underline-offset-4">
            Edit in Settings
          </Link>
        }
      />

      <EmergencyStopControl stopped={automation.emergency_stopped} />

      <Card className="flex flex-col gap-4">
        <CardTitle>Approval rules</CardTitle>
        <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-ink">
          <li>A tool that is not low risk always needs your approval before it runs, even when a device permission says allow.</li>
          <li>A low risk device tool runs without asking only when its device permission is set to allow.</li>
          <li>A device permission set to deny stops that tool on that device.</li>
          <li>Up to {automation.max_concurrent_tasks} tasks run at once.</li>
        </ul>
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-ink">Device permissions</h2>
          {devices.isPending ? <SkeletonList rows={2} label="Loading devices" /> : null}
          {devices.isError ? <ErrorState message={devices.error.message} onRetry={() => void devices.refetch()} /> : null}
          {devices.data && deviceList.length === 0 ? (
            <p className="text-sm text-muted">No devices are paired, so only server tools can run.</p>
          ) : null}
          {deviceList.map((device) => {
            const entries = Object.entries(device.permissions);
            return (
              <div key={device.id} className="flex flex-col gap-2 rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium text-ink">{device.name}</span>
                  <Badge tone={device.connection_status === "online" ? "success" : "neutral"}>{humanize(device.connection_status)}</Badge>
                </div>
                {entries.length === 0 ? (
                  <p className="text-xs text-muted">No tool permissions recorded for this device.</p>
                ) : (
                  <ul aria-label={`Permissions for ${device.name}`} className="flex flex-col gap-1 text-xs">
                    {entries.map(([tool, permission]) => (
                      <li key={tool} className="flex justify-between gap-2">
                        <code className="text-ink">{tool}</code>
                        <span className="text-muted">{humanize(permission.mode)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Tools</CardTitle>
          <span className="text-sm text-muted">
            {enabledCount} of {toolList.length} enabled
          </span>
        </div>
        {toolList.length === 0 ? (
          <EmptyState title="No tools available" description="The tool list is empty." />
        ) : (
          <ul aria-label="Tool policy" className="flex flex-col gap-2">
            {toolList.map((tool) => (
              <ToolRow key={tool.name} tool={tool} enabled={!disabled.has(tool.name)} />
            ))}
          </ul>
        )}
      </Card>

      <p className="text-sm text-muted">
        Recurring work lives on the <Link to="/schedule" className="text-accent underline underline-offset-4">Schedule</Link> page. Device permissions are
        changed on the <Link to="/devices" className="text-accent underline underline-offset-4">Devices</Link> page.
      </p>
    </div>
  );
}
