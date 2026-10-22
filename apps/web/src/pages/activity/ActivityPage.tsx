import { useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input, Select } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { downloadAttachment } from "../../services/exportDownload";
import { api } from "../../services/api";
import { formatDateTime, humanize } from "../../services/format";
import {
  AUDIT_PAGE_SIZE,
  OUTCOME_OPTIONS,
  OUTCOME_TONE,
  auditFilterParams,
  auditQueryString,
  dateRangeParams,
  taskTimelinePath,
  type AuditRow,
} from "./activityModel";

interface AuditPage {
  items: AuditRow[];
  total: number;
}

/** Audit log: who did what, with the outcome and the day it happened. Task entries link to the task's event timeline. */
export default function ActivityPage() {
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedAction = useDebouncedValue(action, 300);
  const { toast } = useToast();
  const [exporting, setExporting] = useState(false);
  const filters = { action: debouncedAction, outcome, from: fromDate, to: toDate, offset };
  const range = dateRangeParams(fromDate, toDate);
  const audit = useQuery({
    queryKey: ["audit", "log", filters] as const,
    queryFn: () => api.get<AuditPage>(`/audit${auditQueryString(filters)}`),
    placeholderData: keepPreviousData,
  });
  const items = audit.data?.items ?? [];
  const total = audit.data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + AUDIT_PAGE_SIZE, total);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Activity"
        description="The audit log for this account: what JARVIS did, what it was asked to do, and whether it worked."
        actions={
          <Button
            variant="secondary"
            loading={exporting}
            disabled={exporting}
            onClick={() => {
              // The CSV carries the same filters as the list. It leaves out IP addresses and event data.
              const query = auditFilterParams(filters);
              setExporting(true);
              downloadAttachment(`/audit/export.csv${query.toString() ? `?${query}` : ""}`, "jarvis-audit.csv")
                .then((name) => toast({ tone: "success", title: "Activity saved", description: name }))
                .catch((error: unknown) =>
                  toast({ tone: "error", title: "Activity not exported", description: error instanceof Error ? error.message : "Try again." }),
                )
                .finally(() => setExporting(false));
            }}
          >
            Export CSV
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-[1fr_200px] sm:items-end">
        <Field label="Action starts with" hint="For example task. or device.">
          {(control) => (
            <Input
              {...control}
              type="search"
              value={action}
              onChange={(event) => {
                setAction(event.target.value);
                setOffset(0);
              }}
            />
          )}
        </Field>
        <Field label="Outcome">
          {(control) => (
            <Select
              {...control}
              value={outcome}
              onChange={(event) => {
                setOutcome(event.target.value);
                setOffset(0);
              }}
            >
              {OUTCOME_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="From date" hint="The day starts at midnight in this browser's time zone.">
          {(control) => (
            <Input
              {...control}
              type="date"
              value={fromDate}
              onChange={(event) => {
                setFromDate(event.target.value);
                setOffset(0);
              }}
            />
          )}
        </Field>
        <Field label="To date" error={range.error ?? null}>
          {(control) => (
            <Input
              {...control}
              type="date"
              invalid={Boolean(range.error)}
              value={toDate}
              onChange={(event) => {
                setToDate(event.target.value);
                setOffset(0);
              }}
            />
          )}
        </Field>
      </div>

      {audit.isPending ? (
        <SkeletonList rows={4} label="Loading activity" />
      ) : audit.isError ? (
        <ErrorState message={audit.error.message} onRetry={() => void audit.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title="No activity matches" description="Try another action prefix, outcome or date range." />
      ) : (
        <>
          <p className="text-sm text-muted" aria-live="polite">
            Showing {from} to {to} of {total}
          </p>
          <ol aria-label="Audit log" className="flex flex-col gap-2">
            {items.map((entry) => {
              const timeline = taskTimelinePath(entry);
              return (
                <li key={String(entry.id)} className="flex flex-col gap-2 rounded-lg border border-line bg-surface p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-ink">{entry.action}</code>
                    <Badge tone={OUTCOME_TONE[entry.outcome] ?? "neutral"}>{humanize(entry.outcome)}</Badge>
                    <span className="text-xs text-muted">{formatDateTime(entry.created_at)}</span>
                  </div>
                  <p className="text-xs text-muted">
                    By {humanize(entry.actor_type)}
                    {entry.target_type ? ` on ${humanize(entry.target_type)}` : ""}
                  </p>
                  {timeline ? (
                    <Link to={timeline} className="w-fit text-xs text-accent underline underline-offset-4">
                      Open the task timeline
                    </Link>
                  ) : null}
                  {Object.keys(entry.data).length > 0 ? (
                    <details className="text-xs text-muted">
                      <summary className="cursor-pointer text-ink">Details</summary>
                      <pre className="mt-2 overflow-x-auto rounded bg-raised p-2 text-xs">{JSON.stringify(entry.data, null, 2)}</pre>
                    </details>
                  ) : null}
                </li>
              );
            })}
          </ol>
          <nav aria-label="Activity pages" className="flex items-center justify-between gap-2">
            <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))}>
              Previous
            </Button>
            <Button variant="secondary" size="sm" disabled={offset + AUDIT_PAGE_SIZE >= total} onClick={() => setOffset(offset + AUDIT_PAGE_SIZE)}>
              Next
            </Button>
          </nav>
        </>
      )}
    </div>
  );
}
