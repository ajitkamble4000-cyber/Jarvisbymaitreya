import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Icon, type IconName } from "../../components/ui/Icon";
import { SkeletonList } from "../../components/ui/Skeleton";
import { StatusDot, type StatusTone } from "../../components/ui/StatusDot";
import { formatDateTime, formatRelative, humanize } from "../../services/format";
import { useTaskAction } from "../../services/queries";
import type { AuditEntry, DeviceOut, HealthOut, ProviderOut, ScheduleOut, SettingsDoc, TaskCounts, TaskOut } from "../../types/api";
import { TASK_STATUS_LABEL, statusTone, totalCount } from "../tasks/taskModel";
import { describeAudit, upcomingSchedules } from "./overviewModel";

/** Shared frame for an overview card: title, optional link, and body. */
export function OverviewCard({ title, icon, link, children }: { title: string; icon: IconName; link?: { to: string; label: string }; children: ReactNode }) {
  return (
    <Card className="flex min-w-0 flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Icon name={icon} className="size-4 text-accent" />
          <CardTitle className="truncate">{title}</CardTitle>
        </div>
        {link ? (
          <Link to={link.to} className="shrink-0 text-sm text-accent underline-offset-4 hover:underline">
            {link.label}
          </Link>
        ) : null}
      </div>
      {children}
    </Card>
  );
}

export function AssistantStatusCard({
  health,
  unreachable = false,
  provider,
  settings,
}: {
  health: HealthOut | undefined;
  /** True when the latest health request failed. Shown even when older health data exists. */
  unreachable?: boolean;
  provider: ProviderOut | undefined;
  settings: SettingsDoc | undefined;
}) {
  const healthTone: StatusTone = unreachable
    ? "danger"
    : !health
      ? "muted"
      : health.status === "ok" && health.database === "ok"
        ? "success"
        : "warning";
  const healthLabel = unreachable ? "Unreachable" : !health ? "Checking" : health.status === "ok" ? "Healthy" : "Degraded";
  const providerTone: StatusTone = !provider ? "muted" : provider.status === "connected" ? "success" : provider.status === "error" ? "danger" : "warning";
  const providerLabel = !provider
    ? "No active provider selected"
    : provider.status === "connected"
      ? `${provider.display_name} connected`
      : provider.status === "error"
        ? `${provider.display_name} reports an error`
        : provider.status === "untested"
          ? `${provider.display_name} not tested yet`
          : `${provider.display_name} is not configured`;
  return (
    <OverviewCard title="Assistant status" icon="overview" link={{ to: "/models", label: "AI Models" }}>
      <div className="flex flex-col gap-3 text-sm">
        <StatusDot
          tone={healthTone}
          label={`Backend: ${healthLabel}${health && !unreachable ? `, database ${health.database}` : ""}`}
        />
        <StatusDot tone={providerTone} label={providerLabel} />
        <p className="text-muted">
          Fallback {settings?.ai.fallback_enabled && settings.ai.fallback_provider ? `to ${humanize(settings.ai.fallback_provider)}` : "is off"}.
        </p>
      </div>
    </OverviewCard>
  );
}

export function DevicesCard({ devices, isPending, error, onRetry }: { devices: DeviceOut[] | undefined; isPending: boolean; error: Error | null; onRetry: () => void }) {
  return (
    <OverviewCard title="Devices" icon="devices" link={{ to: "/devices", label: "Manage" }}>
      {isPending ? (
        <SkeletonList rows={2} label="Loading devices" />
      ) : error ? (
        <ErrorState message={error.message} onRetry={onRetry} />
      ) : !devices || devices.length === 0 ? (
        <EmptyState title="No devices paired" description="Pair the Windows agent to let JARVIS open apps and folders on your PC." action={<Link to="/devices?pair=1" className="text-sm text-accent underline underline-offset-4">Connect a device</Link>} />
      ) : (
        <>
          <p className="text-sm text-muted">
            {devices.filter((device) => device.connection_status === "online").length} of {devices.length} online
          </p>
          <ul className="flex flex-col gap-2">
            {devices.slice(0, 5).map((device) => (
              <li key={device.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0 truncate text-ink">{device.name}</span>
                <StatusDot
                  tone={device.connection_status === "online" ? "success" : device.connection_status === "revoked" ? "danger" : "muted"}
                  label={humanize(device.connection_status)}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </OverviewCard>
  );
}

export function TasksCard({ counts, items, isPending, error, onRetry }: { counts: TaskCounts | undefined; items: TaskOut[] | undefined; isPending: boolean; error: Error | null; onRetry: () => void }) {
  return (
    <OverviewCard title="Recent tasks" icon="tasks" link={{ to: "/tasks", label: "All tasks" }}>
      {isPending ? (
        <SkeletonList rows={3} label="Loading tasks" />
      ) : error ? (
        <ErrorState message={error.message} onRetry={onRetry} />
      ) : (
        <>
          <p className="text-sm text-muted">{counts ? `${totalCount(counts)} tasks, ${counts.running} running, ${counts.failed} failed` : "No task counts yet"}</p>
          {!items || items.length === 0 ? (
            <p className="text-sm text-muted">No tasks yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {items.map((task) => (
                <li key={task.id} className="flex min-w-0 items-center justify-between gap-2 text-sm">
                  <Link to={`/tasks/${task.id}`} className="min-w-0 truncate text-ink hover:text-accent">
                    {task.title}
                  </Link>
                  <StatusDot tone={statusTone(task.status)} label={TASK_STATUS_LABEL[task.status]} className="shrink-0 text-xs" />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </OverviewCard>
  );
}

function ApprovalRow({ task }: { task: TaskOut }) {
  const action = useTaskAction();
  const pending = action.isPending ? action.variables?.action : null;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-warning/40 bg-warning/5 p-3">
      <p className="text-sm font-medium text-ink">{task.title}</p>
      <p className="text-xs text-muted">Expires {task.approval_expires_at ? formatRelative(task.approval_expires_at) : "after the approval window"}</p>
      <div className="flex gap-2">
        <Button size="sm" variant="primary" disabled={!task.actions.approve || action.isPending} loading={pending === "approve"} onClick={() => action.mutate({ id: task.id, action: "approve" })}>
          Approve
        </Button>
        <Button size="sm" variant="danger" disabled={!task.actions.deny || action.isPending} loading={pending === "deny"} onClick={() => action.mutate({ id: task.id, action: "deny" })}>
          Deny
        </Button>
        <Link to={`/tasks/${task.id}`} className="self-center text-sm text-accent underline underline-offset-4">
          Details
        </Link>
      </div>
      {action.isError && action.variables?.id === task.id ? <p role="alert" className="text-xs font-medium text-danger">{action.error.message}</p> : null}
    </li>
  );
}

export function ApprovalsCard({ items, total, isPending, error, onRetry }: { items: TaskOut[] | undefined; total: number; isPending: boolean; error: Error | null; onRetry: () => void }) {
  return (
    <OverviewCard title="Waiting for you" icon="alert" link={{ to: "/tasks?status=awaiting_approval", label: "Review" }}>
      {isPending ? (
        <SkeletonList rows={2} label="Loading approvals" />
      ) : error ? (
        <ErrorState message={error.message} onRetry={onRetry} />
      ) : !items || items.length === 0 ? (
        <p className="text-sm text-muted">Nothing is waiting for your approval.</p>
      ) : (
        <>
          {total > items.length ? <Badge tone="warning">{total} waiting</Badge> : null}
          <ul className="flex flex-col gap-3">
            {items.map((task) => (
              <ApprovalRow key={task.id} task={task} />
            ))}
          </ul>
        </>
      )}
    </OverviewCard>
  );
}

export function ScheduleCard({ schedules, isPending, error, onRetry }: { schedules: ScheduleOut[] | undefined; isPending: boolean; error: Error | null; onRetry: () => void }) {
  const upcoming = schedules ? upcomingSchedules(schedules) : [];
  return (
    <OverviewCard title="Schedule" icon="schedule" link={{ to: "/schedule", label: "Open schedule" }}>
      {isPending ? (
        <SkeletonList rows={2} label="Loading schedule" />
      ) : error ? (
        <ErrorState message={error.message} onRetry={onRetry} />
      ) : upcoming.length === 0 ? (
        <p className="text-sm text-muted">No upcoming scheduled actions.</p>
      ) : (
        <ul className="flex flex-col gap-2 text-sm">
          {upcoming.map((schedule) => (
            <li key={schedule.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-ink">{schedule.name}</span>
              <time className="shrink-0 text-xs text-muted" dateTime={schedule.next_run_at ?? undefined}>
                {formatDateTime(schedule.next_run_at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </OverviewCard>
  );
}

export function ActivityCard({ entries, isPending, error, onRetry }: { entries: AuditEntry[] | undefined; isPending: boolean; error: Error | null; onRetry: () => void }) {
  return (
    <OverviewCard title="Recent activity" icon="activity" link={{ to: "/activity", label: "All activity" }}>
      {isPending ? (
        <SkeletonList rows={3} label="Loading activity" />
      ) : error ? (
        <ErrorState message={error.message} onRetry={onRetry} />
      ) : !entries || entries.length === 0 ? (
        <p className="text-sm text-muted">No activity recorded yet.</p>
      ) : (
        <ul className="flex flex-col gap-2 text-sm">
          {entries.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-ink">{describeAudit(entry)}</span>
              <time className="shrink-0 text-xs text-muted" dateTime={entry.created_at}>
                {formatRelative(entry.created_at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </OverviewCard>
  );
}

export function QuickActionsCard() {
  const actions: Array<{ to: string; label: string; icon: IconName }> = [
    { to: "/voice", label: "Start voice", icon: "voice" },
    { to: "/chat", label: "Open chat", icon: "chat" },
    { to: "/tasks?create=1", label: "Create task", icon: "plus" },
    { to: "/schedule", label: "Add schedule", icon: "schedule" },
    { to: "/devices?pair=1", label: "Connect a device", icon: "devices" },
    { to: "/settings", label: "Open settings", icon: "settings" },
  ];
  return (
    <OverviewCard title="Quick actions" icon="play">
      <div className="grid grid-cols-2 gap-2">
        {actions.map((action) => (
          <Link
            key={action.to}
            to={action.to}
            className="flex items-center gap-2 rounded-lg border border-line bg-raised px-3 py-2 text-sm font-medium text-ink hover:border-accent"
          >
            <Icon name={action.icon} className="size-4 text-accent" />
            {action.label}
          </Link>
        ))}
      </div>
    </OverviewCard>
  );
}
