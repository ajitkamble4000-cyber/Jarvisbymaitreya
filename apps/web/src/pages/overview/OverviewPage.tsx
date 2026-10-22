import { PageHeader } from "../../components/layout/PageHeader";
import { AIOrb } from "../../components/orb/AIOrb";
import { Card } from "../../components/ui/Card";
import { ErrorState } from "../../components/ui/ErrorState";
import {
  useAuditQuery,
  useConversationsQuery,
  useDevicesQuery,
  useHealthQuery,
  useProvidersQuery,
  useSchedulesQuery,
  useSettingsQuery,
  useTasksQuery,
} from "../../services/queries";
import { ActivityCard, ApprovalsCard, AssistantStatusCard, DevicesCard, QuickActionsCard, ScheduleCard, TasksCard } from "./cards";
import { OnboardingCard } from "./OnboardingCard";
import { onboardingSteps } from "./onboarding";
import { DEFAULT_WIDGETS, deriveAssistantState, findActiveProvider } from "./overviewModel";

/**
 * Overview: assistant status, devices, tasks, approvals, schedule, activity and quick actions.
 * Every number comes from a real endpoint. Widgets follow settings.appearance.dashboard_widgets.
 */
export default function OverviewPage() {
  const settings = useSettingsQuery();
  const health = useHealthQuery();
  const providers = useProvidersQuery();
  const devices = useDevicesQuery();
  const tasks = useTasksQuery({ limit: 6 });
  const approvals = useTasksQuery({ status: "awaiting_approval", limit: 5 });
  const schedules = useSchedulesQuery();
  const audit = useAuditQuery(8);
  const conversations = useConversationsQuery({ query: "", archived: false });

  const widgets = settings.data?.appearance.dashboard_widgets ?? DEFAULT_WIDGETS;
  const activeProvider = findActiveProvider(providers.data, settings.data?.ai.active_provider);
  // Any failed health request means the backend cannot be confirmed, including a 502 from the proxy when the API
  // container is down. The query keeps its last good data during a failed refetch, so the error is checked here.
  const offline = health.isError;
  const counts = tasks.data?.counts;
  const orbState = deriveAssistantState({
    offline,
    awaitingApproval: counts?.awaiting_approval ?? 0,
    running: counts?.running ?? 0,
    degraded: health.data?.status === "degraded" || activeProvider?.status === "error",
  });

  if (settings.isError && !settings.data) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Overview" />
        <ErrorState message={settings.error.message} onRetry={() => void settings.refetch()} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Overview" description="Your assistant at a glance: what it is doing, what needs you, and what is next." />

      {settings.data && providers.data && devices.data && conversations.data ? (
        <OnboardingCard
          steps={onboardingSteps({
            providers: providers.data,
            devices: devices.data,
            timezone: settings.data.timezone,
            conversationCount: conversations.data.total,
          })}
        />
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="flex min-h-64 items-center justify-center">
          <AIOrb
            state={orbState}
            style={settings.data?.appearance.orb_style}
            intensity={settings.data?.appearance.animation_intensity}
          />
        </Card>
        {widgets.status ? (
          <AssistantStatusCard
            health={health.data}
            unreachable={health.isError}
            provider={activeProvider}
            settings={settings.data}
          />
        ) : null}
        {widgets.quick_actions ? <QuickActionsCard /> : null}
      </div>

      {widgets.tasks ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <ApprovalsCard
            items={approvals.data?.items}
            total={approvals.data?.total ?? 0}
            isPending={approvals.isPending}
            error={approvals.error}
            onRetry={() => void approvals.refetch()}
          />
          <TasksCard
            counts={counts}
            items={tasks.data?.items}
            isPending={tasks.isPending}
            error={tasks.error}
            onRetry={() => void tasks.refetch()}
          />
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        {widgets.devices ? (
          <DevicesCard devices={devices.data} isPending={devices.isPending} error={devices.error} onRetry={() => void devices.refetch()} />
        ) : null}
        {widgets.schedule ? (
          <ScheduleCard schedules={schedules.data} isPending={schedules.isPending} error={schedules.error} onRetry={() => void schedules.refetch()} />
        ) : null}
        {widgets.activity ? (
          <ActivityCard entries={audit.data?.items} isPending={audit.isPending} error={audit.error} onRetry={() => void audit.refetch()} />
        ) : null}
      </div>
    </div>
  );
}
