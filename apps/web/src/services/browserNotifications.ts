/**
 * Browser pop-up alerts for live events. The rules are pure so that they can be tested without a
 * browser: an alert is made only while the tab is hidden, only for the events the settings allow,
 * and only with task details when include_task_details is on. Browser permission is read here
 * and never assumed.
 */

import type { LiveEvent, NotificationSettings } from "../types/api";
import { quietAt } from "./quietHours";

export type BrowserPermission = "default" | "granted" | "denied";

export interface BrowserAlert {
  title: string;
  body: string;
  /** Same tag for the same task, so a repeated alert replaces the earlier one. */
  tag: string;
}

export type AlertSettings = Pick<
  NotificationSettings,
  "desktop_notifications" | "on_approval_required" | "on_task_completed" | "on_task_failed" | "include_task_details"
> &
  // Optional so that a caller without quiet hours behaves as before.
  Partial<Pick<NotificationSettings, "quiet_hours_enabled" | "quiet_hours_start" | "quiet_hours_end">>;

export interface AlertContext {
  /** True when document.hidden is true. Alerts are never made for a visible tab. */
  hidden: boolean;
  /** The reminder time zone from settings. Null uses this browser's zone for quiet hours. */
  timeZone?: string | null;
  /** The moment to test against quiet hours. Defaults to now. */
  now?: Date;
  /** Returns a task title the page already knows, or null. Used only when task details are allowed. */
  taskTitle?: (taskId: string) => string | null;
}

const MAX_TITLE_LENGTH = 120;

function clip(text: string): string {
  return text.length > MAX_TITLE_LENGTH ? `${text.slice(0, MAX_TITLE_LENGTH - 1)}…` : text;
}

/**
 * The alert for one live event, or null when no alert applies. Pure.
 * Rules: the setting must be on for the event, the tab must be hidden, and the text is generic
 * unless include_task_details is true.
 */
export function browserAlertFor(event: LiveEvent, settings: AlertSettings, context: AlertContext): BrowserAlert | null {
  if (!settings.desktop_notifications || !context.hidden) return null;
  // Quiet hours pause pop-ups only. The event still reaches the inbox, which the API sends regardless.
  const quiet = quietAt(
    {
      enabled: settings.quiet_hours_enabled ?? false,
      start: settings.quiet_hours_start ?? null,
      end: settings.quiet_hours_end ?? null,
    },
    context.now ?? new Date(),
    context.timeZone ?? null,
  );
  if (quiet) return null;

  if (event.type === "approval.requested") {
    if (!settings.on_approval_required) return null;
    const tag = `approval:${event.data.task_id}`;
    if (settings.include_task_details) {
      return { title: "Approval needed", body: clip(`${event.data.title}. Open JARVIS to approve or deny it.`), tag };
    }
    return { title: "Approval needed", body: "A task is waiting for your decision. Open JARVIS to review it.", tag };
  }

  if (event.type === "task.updated") {
    const { status, task_id: taskId } = event.data;
    const finished = status === "completed" ? "completed" : status === "failed" ? "failed" : null;
    if (!finished) return null;
    if (finished === "completed" && !settings.on_task_completed) return null;
    if (finished === "failed" && !settings.on_task_failed) return null;

    const tag = `task:${taskId}`;
    const outcome = finished === "completed" ? "Task completed" : "Task failed";
    if (settings.include_task_details) {
      const known = context.taskTitle?.(taskId) ?? null;
      if (known) return { title: outcome, body: clip(known), tag };
    }
    return { title: outcome, body: "Open JARVIS to see the result.", tag };
  }

  return null;
}

/** Permission as the browser reports it, or "unsupported" when the Notification API is missing. */
export function browserNotificationSupport(): BrowserPermission | "unsupported" {
  if (typeof Notification === "undefined") return "unsupported";
  const permission = Notification.permission;
  return permission === "granted" || permission === "denied" ? permission : "default";
}

/**
 * Asks the browser for permission. Call it from a click handler: browsers reject a request that is
 * not made from a user gesture. Resolves with the result, or "unsupported".
 */
export function requestBrowserPermission(): Promise<BrowserPermission | "unsupported"> {
  if (typeof Notification === "undefined" || typeof Notification.requestPermission !== "function") {
    return Promise.resolve("unsupported");
  }
  return Promise.resolve(Notification.requestPermission()).then((result) =>
    result === "granted" || result === "denied" ? result : "default",
  );
}

/** Shows the alert when permission is granted. Never throws: a browser that refuses is not an app error. */
export function showBrowserAlert(alert: BrowserAlert): boolean {
  if (browserNotificationSupport() !== "granted") return false;
  try {
    new Notification(alert.title, { body: alert.body, tag: alert.tag });
    return true;
  } catch {
    return false;
  }
}
