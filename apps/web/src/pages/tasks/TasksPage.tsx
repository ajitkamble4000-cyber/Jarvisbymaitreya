import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { StatusDot } from "../../components/ui/StatusDot";
import { Tabs } from "../../components/ui/Tabs";
import { useToast } from "../../components/ui/Toast";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { formatRelative } from "../../services/format";
import { downloadTasksCsv } from "../../services/exportDownload";
import { useTaskAction, useTasksQuery } from "../../services/queries";
import type { TaskOut } from "../../types/api";
import { CreateTaskDialog } from "./CreateTaskDialog";
import { TaskDetail } from "./TaskDetail";
import { TASK_FILTERS, TASK_STATUS_LABEL, buildTaskQuery, countForFilter, describeTask, statusTone, taskActionStates, taskFilterFromParam, totalCount, type TaskFilterId } from "./taskModel";

function TaskRow({ task, selected }: { task: TaskOut; selected: boolean }) {
  const action = useTaskAction();
  const pendingAction = action.isPending ? (action.variables?.action ?? null) : null;
  const states = taskActionStates(task, pendingAction);
  return (
    <li
      className={
        selected
          ? "flex flex-col gap-2 rounded-lg border border-accent bg-raised p-3"
          : "flex flex-col gap-2 rounded-lg border border-line bg-surface p-3 hover:border-accent/60"
      }
    >
      <div className="flex min-w-0 flex-col gap-1">
        <Link to={`/tasks/${task.id}`} aria-current={selected ? "true" : undefined} className="min-w-0 text-sm font-medium text-ink hover:text-accent">
          <span className="block truncate">{task.title}</span>
        </Link>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <StatusDot tone={statusTone(task.status)} label={TASK_STATUS_LABEL[task.status]} className="text-xs" />
          {task.device ? <Badge>{task.device.name}</Badge> : null}
          <span>{formatRelative(task.created_at)}</span>
        </div>
        <p className="line-clamp-2 text-xs text-muted">{describeTask(task)}</p>
      </div>
      {task.status === "awaiting_approval" || task.actions.retry ? (
        <div className="flex flex-wrap gap-2" aria-label={`Actions for ${task.title}`}>
          {task.status === "awaiting_approval" ? (
            <>
              <Button size="sm" variant="primary" disabled={!states.approve.enabled} loading={pendingAction === "approve"} onClick={() => action.mutate({ id: task.id, action: "approve" })}>
                Approve
              </Button>
              <Button size="sm" variant="danger" disabled={!states.deny.enabled} loading={pendingAction === "deny"} onClick={() => action.mutate({ id: task.id, action: "deny" })}>
                Deny
              </Button>
            </>
          ) : null}
          {task.actions.retry ? (
            <Button size="sm" variant="secondary" disabled={!states.retry.enabled} loading={pendingAction === "retry"} onClick={() => action.mutate({ id: task.id, action: "retry" })}>
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}
      {action.isError && action.variables?.id === task.id ? (
        <p role="alert" className="text-xs font-medium text-danger">
          {action.error.message}
        </p>
      ) : null}
    </li>
  );
}

/** Task list with status tabs, search, live refresh, and a detail view for the selected task. */
export default function TasksPage() {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // ?status= lets other pages link to a filtered list, for example Overview's approvals card.
  const [filter, setFilter] = useState<TaskFilterId>(() => taskFilterFromParam(params.get("status")));
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const { toast } = useToast();
  const [exporting, setExporting] = useState(false);

  // The CSV carries the filters on screen: the status tab and the search text.
  const exportCsv = async (): Promise<void> => {
    setExporting(true);
    try {
      const name = await downloadTasksCsv({ status: filter, q: debouncedSearch });
      toast({ tone: "success", title: "Task list saved", description: name });
    } catch (error) {
      toast({ tone: "error", title: "Task list not exported", description: error instanceof Error ? error.message : "Try again." });
    } finally {
      setExporting(false);
    }
  };
  const query = buildTaskQuery(filter, debouncedSearch);
  const list = useTasksQuery(query);
  const createOpen = params.get("create") === "1";

  const counts = list.data?.counts;
  const total = counts ? totalCount(counts) : 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Tasks"
        description="Actions JARVIS proposes or runs. A device action waits for your approval whenever the device permission asks for it."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" loading={exporting} disabled={exporting} onClick={() => void exportCsv()}>
              Export CSV
            </Button>
            <Button variant="primary" onClick={() => setParams({ create: "1" })}>
              New task
            </Button>
          </div>
        }
      />

      <Tabs
        label="Filter tasks by status"
        value={filter}
        onChange={(id) => setFilter(id as TaskFilterId)}
        items={TASK_FILTERS.map((item) => ({ id: item.id, label: item.label, count: countForFilter(counts, item.id) }))}
      />

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Input type="search" aria-label="Search tasks" placeholder="Search by title or tool" value={search} onChange={(event) => setSearch(event.target.value)} className="sm:max-w-sm" />
        <p role="status" aria-live="polite" className="text-sm text-muted">
          {list.data ? `${list.data.total} shown, ${total} in total` : ""}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <section aria-label="Task list" className="min-w-0">
          {list.isPending ? (
            <SkeletonList rows={4} label="Loading tasks" />
          ) : list.isError ? (
            <ErrorState message={list.error.message} onRetry={() => void list.refetch()} />
          ) : list.data.items.length === 0 ? (
            <EmptyState
              title={debouncedSearch || filter !== "all" ? "No tasks match these filters" : "No tasks yet"}
              description="Tasks appear here when you ask JARVIS to do something on a device, or when a schedule runs."
              action={
                <Button variant="secondary" size="sm" onClick={() => setParams({ create: "1" })}>
                  Create a task
                </Button>
              }
            />
          ) : (
            <ul className="flex flex-col gap-3">
              {list.data.items.map((task) => (
                <TaskRow key={task.id} task={task} selected={task.id === taskId} />
              ))}
            </ul>
          )}
        </section>

        <Card className="min-w-0 self-start lg:sticky lg:top-6">
          {taskId ? (
            <TaskDetail key={taskId} taskId={taskId} onClose={() => navigate({ pathname: "/tasks", search: window.location.search })} />
          ) : (
            <p className="text-sm text-muted">Select a task to see its timeline, result and actions.</p>
          )}
        </Card>
      </div>

      <CreateTaskDialog
        open={createOpen}
        onClose={() => setParams({})}
        onCreated={(task) => {
          setParams({});
          navigate(`/tasks/${task.id}`);
        }}
      />
    </div>
  );
}
