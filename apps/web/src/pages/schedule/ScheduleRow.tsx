import { useState } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useToast } from "../../components/ui/Toast";
import { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import type { ScheduleOut } from "../../types/api";
import { ScheduleRunsPanel } from "./ScheduleRunsPanel";
import { STATE_LABEL, STATE_TONE, describeSchedule, supportsPauseToggle } from "./scheduleModel";
import { useDeleteSchedule, useUpdateSchedule } from "./scheduleQueries";

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "The change could not be saved.";
}

/** One schedule: state, timing, last run, and the pause, enable, history and delete controls. */
export function ScheduleRow({ schedule }: { schedule: ScheduleOut }) {
  const update = useUpdateSchedule();
  const remove = useDeleteSchedule();
  const { toast } = useToast();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const busy = update.isPending || remove.isPending;

  const change = (patch: { paused?: boolean; enabled?: boolean }, success: string): void => {
    update.mutate(
      { id: schedule.id, patch },
      {
        onSuccess: () => toast({ tone: "success", title: success }),
        onError: (cause) => toast({ tone: "error", title: "Schedule not updated", description: errorText(cause) }),
      },
    );
  };

  const confirmDelete = (): void => {
    remove.mutate(schedule.id, {
      onSuccess: () => {
        setConfirmingDelete(false);
        toast({ tone: "success", title: `Deleted "${schedule.name}"` });
      },
      onError: (cause) => toast({ tone: "error", title: "Schedule not deleted", description: errorText(cause) }),
    });
  };

  const finished = schedule.state === "completed";
  const historyId = `runs-${schedule.id}`;

  return (
    <li className="flex flex-col gap-3 rounded-card border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-semibold text-ink">{schedule.name}</h2>
            <Badge tone={STATE_TONE[schedule.state]}>{STATE_LABEL[schedule.state]}</Badge>
          </div>
          {schedule.description ? <p className="text-sm text-muted">{schedule.description}</p> : null}
          <p className="text-sm text-muted">
            <span className="font-mono text-xs text-ink">{schedule.tool_name}</span>
            {schedule.device_id ? " on a device" : " on the server"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {supportsPauseToggle(schedule.state) ? (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => change({ paused: !schedule.paused }, schedule.paused ? "Schedule resumed" : "Schedule paused")}>
              {schedule.paused ? "Resume" : "Pause"}
            </Button>
          ) : null}
          {!finished ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => change({ enabled: !schedule.enabled }, schedule.enabled ? "Schedule disabled" : "Schedule enabled")}
            >
              {schedule.enabled ? "Disable" : "Enable"}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" aria-expanded={historyOpen} aria-controls={historyId} onClick={() => setHistoryOpen((open) => !open)}>
            {historyOpen ? "Hide history" : "Run history"}
          </Button>
          <Button size="sm" variant="danger" disabled={busy} onClick={() => setConfirmingDelete(true)}>
            Delete
          </Button>
        </div>
      </div>

      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted">Schedule</dt>
          <dd className="text-ink">{describeSchedule(schedule.schedule)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Next run</dt>
          <dd className="text-ink">{schedule.next_run_at ? formatDateTime(schedule.next_run_at) : finished ? "Finished" : "None"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Last run</dt>
          <dd className="text-ink">
            {schedule.last_run_at ? `${formatDateTime(schedule.last_run_at)}${schedule.last_status ? `, ${schedule.last_status}` : ""}` : "Never"}
          </dd>
        </div>
      </dl>

      {schedule.consecutive_failures > 0 ? (
        <p className="text-sm font-medium text-danger">
          {schedule.consecutive_failures} failed {schedule.consecutive_failures === 1 ? "run" : "runs"} in a row.
        </p>
      ) : null}

      {historyOpen ? (
        <div id={historyId} className="border-t border-line pt-3">
          <ScheduleRunsPanel scheduleId={schedule.id} />
        </div>
      ) : null}

      <Dialog
        open={confirmingDelete}
        onClose={() => setConfirmingDelete(false)}
        title={`Delete "${schedule.name}"?`}
        description="The schedule and its run history are removed. Nothing runs from it again."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmingDelete(false)}>
              Keep schedule
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={confirmDelete}>
              Delete schedule
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted">This cannot be undone.</p>
      </Dialog>
    </li>
  );
}
