import { useMemo, useState } from "react";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { useSchedulesQuery } from "../../services/queries";
import { ScheduleForm } from "./ScheduleForm";
import { ScheduleRow } from "./ScheduleRow";
import { sortByNextRun } from "./scheduleModel";

/** Schedules: create, pause or resume, enable or disable, delete, and see each run. */
export default function SchedulePage() {
  const schedules = useSchedulesQuery();
  const { toast } = useToast();
  const [creating, setCreating] = useState(false);
  const items = useMemo(() => sortByNextRun(schedules.data ?? []), [schedules.data]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Schedule"
        description="Run a tool once, daily or weekly. Each run creates a task. Device tools still ask for approval at each run unless their permission is set to allow and the tool is low risk."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Icon name="plus" className="size-4" />
            New schedule
          </Button>
        }
      />

      {schedules.isPending ? (
        <SkeletonList rows={3} label="Loading schedules" />
      ) : schedules.isError ? (
        <ErrorState message={schedules.error.message} onRetry={() => void schedules.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="No schedules yet"
          description="Create one to run a tool at a set time. Schedules are listed here, soonest first."
          action={
            <Button variant="secondary" onClick={() => setCreating(true)}>
              Create a schedule
            </Button>
          }
        />
      ) : (
        <ul aria-label="Schedules, soonest first" className="flex flex-col gap-4">
          {items.map((schedule) => (
            <ScheduleRow key={schedule.id} schedule={schedule} />
          ))}
        </ul>
      )}

      <Dialog open={creating} onClose={() => setCreating(false)} title="New schedule" description="Pick a tool and when it should run." size="lg">
        <ScheduleForm
          onCancel={() => setCreating(false)}
          onSaved={(name) => {
            setCreating(false);
            toast({ tone: "success", title: `Scheduled "${name}"` });
          }}
        />
      </Dialog>
    </div>
  );
}
