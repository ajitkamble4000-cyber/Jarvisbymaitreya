import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Tabs } from "../../components/ui/Tabs";
import { useToast } from "../../components/ui/Toast";
import { ApiError } from "../../services/api";
import { formatRelative, humanize } from "../../services/format";
import {
  useDismissNotification,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotificationsQuery,
  type NotificationItem,
} from "./notificationsQueries";

const SEVERITY_LABEL: Record<string, string> = { info: "Info", success: "Success", warning: "Warning", error: "Error" };
const SEVERITY_TONE: Record<string, BadgeTone> = { info: "info", success: "success", warning: "warning", error: "danger" };

function NotificationRow({ item }: { item: NotificationItem }) {
  const markRead = useMarkNotificationRead();
  const dismiss = useDismissNotification();
  const { toast } = useToast();
  const failed = (cause: unknown) =>
    toast({ tone: "error", title: "Notification not updated", description: cause instanceof ApiError ? cause.message : "Try again." });

  return (
    <li
      className={
        item.read
          ? "flex flex-col gap-3 rounded-card border border-line bg-surface p-4"
          : "flex flex-col gap-3 rounded-card border border-accent/60 bg-surface p-4"
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={SEVERITY_TONE[item.severity] ?? "neutral"}>{SEVERITY_LABEL[item.severity] ?? humanize(item.severity)}</Badge>
        {item.read ? null : (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-accent">
            <span aria-hidden="true" className="size-2 rounded-full bg-accent" />
            Unread
          </span>
        )}
        <span className="text-xs text-muted">{formatRelative(item.created_at)}</span>
      </div>
      <div className="min-w-0">
        <h2 className={item.read ? "text-sm font-medium text-ink" : "text-sm font-semibold text-ink"}>{item.title}</h2>
        {item.body ? <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{item.body}</p> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {item.related_type === "task" && item.related_id ? (
          <Link to={`/tasks/${item.related_id}`} className="text-sm text-accent underline underline-offset-4">
            View task
          </Link>
        ) : item.related_type ? (
          <span className="text-xs text-muted">Related: {humanize(item.related_type)}</span>
        ) : null}
        <div className="ml-auto flex flex-wrap gap-2">
          {item.read ? null : (
            <Button
              size="sm"
              variant="secondary"
              loading={markRead.isPending}
              onClick={() => markRead.mutate(item.id, { onError: failed })}
            >
              Mark read
            </Button>
          )}
          <Button size="sm" variant="ghost" loading={dismiss.isPending} onClick={() => dismiss.mutate(item.id, { onError: failed })}>
            Dismiss
          </Button>
        </div>
      </div>
    </li>
  );
}

/** Inbox of notifications with an unread filter, mark read, read all and dismiss. */
export default function NotificationsPage() {
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const unreadOnly = filter === "unread";
  const inbox = useNotificationsQuery(unreadOnly);
  const readAll = useMarkAllNotificationsRead();
  const { toast } = useToast();
  const unreadCount = inbox.data?.unread_count ?? 0;
  const items = inbox.data?.items ?? [];
  const hiddenCount = inbox.data?.hidden_by_preferences ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Notifications"
        description="Things JARVIS wants you to know about: finished tasks, approvals waiting, devices that dropped off."
        actions={
          <Button
            variant="secondary"
            disabled={unreadCount === 0}
            loading={readAll.isPending}
            onClick={() =>
              readAll.mutate(undefined, {
                onSuccess: (result) => toast({ tone: "success", title: `Marked ${result.updated} as read` }),
                onError: (cause) => toast({ tone: "error", title: "Could not mark all as read", description: cause.message }),
              })
            }
          >
            Mark all read
          </Button>
        }
      />

      <Tabs
        label="Filter notifications"
        value={filter}
        onChange={(id) => setFilter(id === "unread" ? "unread" : "all")}
        items={[
          { id: "all", label: "All" },
          { id: "unread", label: "Unread", count: unreadCount },
        ]}
      />

      {hiddenCount > 0 ? (
        <p role="status" className="text-sm text-muted">
          {hiddenCount === 1 ? "1 notification is hidden by your settings." : `${hiddenCount} notifications are hidden by your settings.`}{" "}
          <Link to="/settings?tab=notifications" className="text-accent underline underline-offset-4">
            Change notification settings
          </Link>
        </p>
      ) : null}

      {inbox.isPending ? (
        <SkeletonList rows={3} label="Loading notifications" />
      ) : inbox.isError ? (
        <ErrorState message={inbox.error.message} onRetry={() => void inbox.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title={unreadOnly ? "No unread notifications" : "No notifications yet"}
          description={unreadOnly ? "You are up to date." : "Notifications appear here when a task finishes, needs approval or a device changes."}
        />
      ) : (
        <ul aria-label="Notifications" className="flex flex-col gap-3">
          {items.map((item) => (
            <NotificationRow key={item.id} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}
