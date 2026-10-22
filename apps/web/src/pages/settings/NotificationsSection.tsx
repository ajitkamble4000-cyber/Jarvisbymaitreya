import { useState } from "react";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Switch } from "../../components/ui/Switch";
import { ImmediateSwitch, SaveBar, SettingsCard } from "./SettingsShared";
import { changedKeys } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";
import { parseClock } from "../../services/quietHours";
import { browserNotificationSupport, requestBrowserPermission, type BrowserPermission } from "../../services/browserNotifications";
import type { NotificationSettings } from "../../types/api";

type EventKey = "on_task_completed" | "on_task_failed" | "on_approval_required" | "on_device_disconnected" | "on_schedule_event";

const EVENT_ROWS: Array<{ key: EventKey; label: string; description: string }> = [
  { key: "on_task_completed", label: "A task finishes", description: "Sent when a task completes successfully." },
  { key: "on_task_failed", label: "A task fails", description: "Sent when a task ends with an error." },
  { key: "on_approval_required", label: "Approval is needed", description: "Sent when a task waits for your decision." },
  { key: "on_device_disconnected", label: "A device goes offline", description: "Sent when a paired desktop agent disconnects." },
  { key: "on_schedule_event", label: "A schedule runs or fails", description: "Sent for scheduled runs." },
];

/** Describes the browser permission in words. Alerts appear only when it reads "granted". */
function permissionText(permission: BrowserPermission | "unsupported"): string {
  if (permission === "unsupported") return "This browser does not support desktop notifications.";
  if (permission === "granted") return "Browser permission: granted. Alerts appear while the JARVIS tab is hidden.";
  if (permission === "denied") return "Browser permission: denied. Allow notifications for this site in the browser settings to get alerts.";
  return "Browser permission: not decided yet. Turning the switch on asks the browser.";
}

/**
 * Quiet hours for pop-ups. The times are "HH:MM" in the reminder time zone, or this browser's zone when none
 * is set. The inbox is not affected. Saves only the keys that changed.
 */
function QuietHoursCard({ notifications, timezone }: { notifications: NotificationSettings; timezone: string | null }) {
  return (
    <QuietHoursForm
      key={JSON.stringify([notifications.quiet_hours_enabled, notifications.quiet_hours_start, notifications.quiet_hours_end])}
      notifications={notifications}
      timezone={timezone}
    />
  );
}

function QuietHoursForm({ notifications, timezone }: { notifications: NotificationSettings; timezone: string | null }) {
  const save = useSaveSettings();
  const [enabled, setEnabled] = useState(notifications.quiet_hours_enabled);
  const [start, setStart] = useState(notifications.quiet_hours_start ?? "22:00");
  const [end, setEnd] = useState(notifications.quiet_hours_end ?? "07:00");
  const [error, setError] = useState<string | null>(null);

  const server = {
    quiet_hours_enabled: notifications.quiet_hours_enabled,
    quiet_hours_start: notifications.quiet_hours_start ?? "22:00",
    quiet_hours_end: notifications.quiet_hours_end ?? "07:00",
  };
  const draft = { quiet_hours_enabled: enabled, quiet_hours_start: start, quiet_hours_end: end };
  const dirty = Object.keys(changedKeys(server, draft)).length > 0;

  const onSave = (): void => {
    if (enabled && (parseClock(start) === null || parseClock(end) === null)) {
      setError("Choose both a start time and an end time.");
      return;
    }
    if (enabled && start === end) {
      setError("The quiet hours must start and end at different times.");
      return;
    }
    setError(null);
    const changes = changedKeys(server, draft);
    save.mutate({ notifications: changes });
  };

  const zoneText = timezone ? `the reminder time zone (${timezone})` : "this browser's time zone";

  return (
    <SettingsCard title="Quiet hours" description="Pauses desktop pop-ups in the window. Notifications still appear in the inbox.">
      <Switch
        label="Pause pop-ups during quiet hours"
        description={`Uses ${zoneText}. A window that starts later than it ends runs overnight.`}
        checked={enabled}
        disabled={save.isPending}
        onCheckedChange={setEnabled}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Quiet from" error={error}>
          {(control) => (
            <Input {...control} type="time" step={60} disabled={!enabled} invalid={Boolean(error)} value={start} onChange={(event) => setStart(event.target.value)} />
          )}
        </Field>
        <Field label="Quiet until" hint="The end time is not quiet: pop-ups are allowed again from this minute.">
          {(control) => (
            <Input {...control} type="time" step={60} disabled={!enabled} invalid={Boolean(error)} value={end} onChange={(event) => setEnd(event.target.value)} />
          )}
        </Field>
      </div>
      <SaveBar
        dirty={dirty}
        pending={save.isPending}
        error={save.isError ? save.error.message : null}
        onSave={onSave}
        onDiscard={() => {
          setEnabled(notifications.quiet_hours_enabled);
          setStart(notifications.quiet_hours_start ?? "22:00");
          setEnd(notifications.quiet_hours_end ?? "07:00");
          setError(null);
        }}
      />
    </SettingsCard>
  );
}

export function NotificationsSection({ notifications, timezone = null }: { notifications: NotificationSettings; timezone?: string | null }) {
  const save = useSaveSettings();
  const set = (patch: Partial<NotificationSettings>) => save.mutate({ notifications: patch });
  const [permission, setPermission] = useState<BrowserPermission | "unsupported">(() => browserNotificationSupport());

  // The request runs inside the switch's change handler, which is the user's click. Browsers reject a
  // request that is not made from a click.
  const onDesktopChange = (value: boolean): void => {
    if (value) void requestBrowserPermission().then(setPermission);
    set({ desktop_notifications: value });
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Desktop notifications" description="Pop-up alerts from this browser, shown only while the JARVIS tab is hidden.">
        <ImmediateSwitch
          label="Desktop pop-up alerts"
          description="Alerts for the events you choose below. The text is generic unless you allow task details."
          checked={notifications.desktop_notifications}
          pending={save.isPending}
          onChange={onDesktopChange}
        />
        <p className="text-sm text-muted" aria-live="polite">
          {permissionText(permission)}
        </p>
        <ImmediateSwitch
          label="Include task details in alerts"
          description="When off, alerts use generic text so that task details stay off the screen."
          checked={notifications.include_task_details}
          pending={save.isPending}
          onChange={(value) => set({ include_task_details: value })}
        />
      </SettingsCard>

      <QuietHoursCard notifications={notifications} timezone={timezone} />

      <SettingsCard title="Events" description="Which events create a notification. Saved as soon as you change them.">
        {EVENT_ROWS.map((row) => (
          <ImmediateSwitch
            key={row.key}
            label={row.label}
            description={row.description}
            checked={notifications[row.key]}
            pending={save.isPending}
            onChange={(value) => set({ [row.key]: value } as Partial<NotificationSettings>)}
          />
        ))}
        <ImmediateSwitch
          label="An integration reports an error"
          description="Notifications from a connected integration, such as a failed sign-in. Hides them in the inbox when off."
          checked={notifications.on_integration_error}
          pending={save.isPending}
          onChange={(value) => set({ on_integration_error: value })}
        />
        <ImmediateSwitch
          label="A security event"
          description="Sign-in and security notices. Hides them in the inbox when off."
          checked={notifications.on_security_event}
          pending={save.isPending}
          onChange={(value) => set({ on_security_event: value })}
        />
      </SettingsCard>
    </div>
  );
}
