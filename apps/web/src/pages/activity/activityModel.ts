/** Audit log helpers for the Activity page. Pure, so the filters and links can be tested. */

import type { BadgeTone } from "../../components/ui/Badge";

export interface AuditRow {
  id: number | string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  data: Record<string, unknown>;
  created_at: string;
}

export const OUTCOME_OPTIONS = [
  { value: "", label: "Any outcome" },
  { value: "success", label: "Success" },
  { value: "failure", label: "Failure" },
  { value: "denied", label: "Denied" },
] as const;

export const OUTCOME_TONE: Record<string, BadgeTone> = {
  success: "success",
  failure: "danger",
  denied: "warning",
};

export const AUDIT_PAGE_SIZE = 50;

export interface AuditFilters {
  action: string;
  outcome: string;
  /** Calendar dates as typed in the date inputs, "YYYY-MM-DD", or "" for no bound. */
  from: string;
  to: string;
  offset: number;
}

const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/** The local calendar day as a pair of instants, or null when the text is not a real date. */
function localDayBounds(date: string): { start: Date; end: Date } | null {
  const match = DATE_PATTERN.exec(date);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const start = new Date(year, month - 1, day, 0, 0, 0, 0);
  // Rejects dates that roll over, such as February 31st.
  if (start.getFullYear() !== year || start.getMonth() !== month - 1 || start.getDate() !== day) return null;
  return { start, end: new Date(year, month - 1, day, 23, 59, 59, 999) };
}

export interface DateRangeParams {
  since?: string;
  until?: string;
  /** Shown to the person when the range cannot be used. The range is then left out of the request. */
  error?: string;
}

/**
 * Turns the two date inputs into the server's inclusive instants: the first moment of the first day to the last
 * moment of the last day, in this browser's zone. A bound left empty is not sent.
 */
export function dateRangeParams(from: string, to: string): DateRangeParams {
  if (from && to && from > to) return { error: "The first date is after the last date." };
  const params: DateRangeParams = {};
  if (from) {
    const bounds = localDayBounds(from);
    if (!bounds) return { error: "The first date is not a valid date." };
    params.since = bounds.start.toISOString();
  }
  if (to) {
    const bounds = localDayBounds(to);
    if (!bounds) return { error: "The last date is not a valid date." };
    params.until = bounds.end.toISOString();
  }
  return params;
}

/** The filters both the list and the CSV export send. An empty filter is left out; the action is a prefix match. */
export function auditFilterParams(filters: AuditFilters): URLSearchParams {
  const params = new URLSearchParams();
  const action = filters.action.trim();
  if (action) params.set("action", action);
  if (filters.outcome) params.set("outcome", filters.outcome);
  const range = dateRangeParams(filters.from, filters.to);
  if (range.error === undefined) {
    if (range.since) params.set("since", range.since);
    if (range.until) params.set("until", range.until);
  }
  return params;
}

/** Builds the audit list query: the filters plus the page. */
export function auditQueryString(filters: AuditFilters): string {
  const params = auditFilterParams(filters);
  params.set("limit", String(AUDIT_PAGE_SIZE));
  params.set("offset", String(filters.offset));
  return `?${params.toString()}`;
}

/** Links an entry to its task's event timeline when the entry is about a task. */
export function taskTimelinePath(entry: AuditRow): string | null {
  if (entry.target_type === "task" && entry.target_id) return `/tasks/${encodeURIComponent(entry.target_id)}`;
  const linked = entry.data.task_id;
  if (typeof linked === "string" && linked) return `/tasks/${encodeURIComponent(linked)}`;
  return null;
}
