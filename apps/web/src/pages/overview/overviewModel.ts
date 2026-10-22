import type { AuditEntry, DashboardWidgets, ProviderOut, ScheduleOut } from "../../types/api";
import type { OrbState } from "../../components/orb/AIOrb";

export const DEFAULT_WIDGETS: DashboardWidgets = {
  status: true,
  devices: true,
  tasks: true,
  activity: true,
  schedule: true,
  quick_actions: true,
};

export interface AssistantSignals {
  /** The backend could not be reached at all. */
  offline: boolean;
  awaitingApproval: number;
  running: number;
  /** /health reports degraded, or the active provider reports an error. */
  degraded: boolean;
}

/**
 * Chooses the orb state from real signals. Offline wins over everything, then approvals (they
 * need the viewer), then running work, then degraded health. Pure.
 */
export function deriveAssistantState(signals: AssistantSignals): OrbState {
  if (signals.offline) return "disconnected";
  if (signals.awaitingApproval > 0) return "awaiting_approval";
  if (signals.running > 0) return "executing";
  if (signals.degraded) return "error";
  return "idle";
}

export function findActiveProvider(providers: readonly ProviderOut[] | undefined, activeId: string | undefined): ProviderOut | undefined {
  if (!providers || !activeId) return undefined;
  return providers.find((provider) => provider.id === activeId);
}

/** The schedules that will run next, soonest first, skipping any without a next run. */
export function upcomingSchedules(schedules: readonly ScheduleOut[], limit = 3): ScheduleOut[] {
  return schedules
    .filter((schedule) => schedule.enabled && !schedule.paused && schedule.next_run_at)
    .slice()
    .sort((a, b) => (a.next_run_at ?? "").localeCompare(b.next_run_at ?? ""))
    .slice(0, limit);
}

export function describeAudit(entry: AuditEntry): string {
  const subject = entry.target_type ? `${entry.target_type.replace(/_/g, " ")}` : "";
  const action = entry.action.replace(/[._]/g, " ");
  return subject ? `${action} (${subject})` : action;
}
