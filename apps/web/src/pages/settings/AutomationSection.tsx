import { useMemo, useState } from "react";
import { EmergencyStopControl } from "../automation/EmergencyStopControl";
import { Badge } from "../../components/ui/Badge";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input, Select } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Switch } from "../../components/ui/Switch";
import { useToolsQuery } from "../../services/queries";
import { humanize } from "../../services/format";
import type { AutomationSettings } from "../../types/api";
import { SaveBar, SettingsCard } from "./SettingsShared";
import { changedKeys, parseBoundedInteger, type FieldErrors } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";
import { TIMEZONE_MAX_LENGTH, UTC_OPTION_LABEL, supportedTimeZones, timezoneFromInput, timezoneIsDirty, timezoneToText } from "./timezoneModel";

export function AutomationSection({ automation, timezone }: { automation: AutomationSettings; timezone: string | null }) {
  return <AutomationForm key={JSON.stringify({ automation, timezone })} automation={automation} timezone={timezone} />;
}

function AutomationForm({ automation, timezone }: { automation: AutomationSettings; timezone: string | null }) {
  const save = useSaveSettings();
  const tools = useToolsQuery();
  const [concurrent, setConcurrent] = useState(String(automation.max_concurrent_tasks));
  const [taskTimeout, setTaskTimeout] = useState(String(automation.default_task_timeout_seconds));
  const [errors, setErrors] = useState<FieldErrors>({});

  const parsedConcurrent = parseBoundedInteger(concurrent, 1, 10, "concurrent tasks");
  const parsedTimeout = parseBoundedInteger(taskTimeout, 10, 1800, "task timeout in seconds");
  const dirty = concurrent !== String(automation.max_concurrent_tasks) || taskTimeout !== String(automation.default_task_timeout_seconds);

  // Only changed keys are sent, so a save never rewrites a value the viewer did not touch.
  const onSave = (): void => {
    const found: FieldErrors = {};
    if (parsedConcurrent.error) found.maxConcurrent = parsedConcurrent.error;
    if (parsedTimeout.error) found.timeout = parsedTimeout.error;
    setErrors(found);
    if (Object.keys(found).length > 0 || parsedConcurrent.value === null || parsedTimeout.value === null) return;
    const changes = changedKeys(
      { max_concurrent_tasks: automation.max_concurrent_tasks, default_task_timeout_seconds: automation.default_task_timeout_seconds },
      { max_concurrent_tasks: parsedConcurrent.value, default_task_timeout_seconds: parsedTimeout.value },
    );
    save.mutate({ automation: changes });
  };

  const toggleTool = (name: string, enabled: boolean): void => {
    const current = automation.disabled_tools;
    const next = enabled ? current.filter((item) => item !== name) : [...current, name];
    save.mutate({ automation: { disabled_tools: next } });
  };

  return (
    <div className="flex flex-col gap-6">
      <EmergencyStopControl stopped={automation.emergency_stopped} />

      <TimezoneCard timezone={timezone} />

      <SettingsCard title="Limits" description="How much automation runs at once.">
        <Field label="Tasks running at once" hint="1 to 10." error={errors.maxConcurrent ?? null}>
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              max={10}
              step={1}
              invalid={Boolean(errors.maxConcurrent)}
              value={concurrent}
              onChange={(event) => setConcurrent(event.target.value)}
            />
          )}
        </Field>
        <Field
          label="Default task timeout (seconds)"
          hint="10 to 1800. A sent task still running after this time is marked failed. The desktop agent is not asked to stop."
          error={errors.timeout ?? null}
        >
          {(control) => (
            <Input
              {...control}
              type="number"
              min={10}
              max={1800}
              step={1}
              invalid={Boolean(errors.timeout)}
              value={taskTimeout}
              onChange={(event) => setTaskTimeout(event.target.value)}
            />
          )}
        </Field>
        <SaveBar
          dirty={dirty}
          pending={save.isPending}
          error={save.isError ? save.error.message : null}
          onSave={onSave}
          onDiscard={() => {
            setConcurrent(String(automation.max_concurrent_tasks));
            setTaskTimeout(String(automation.default_task_timeout_seconds));
            setErrors({});
          }}
        />
      </SettingsCard>

      <SettingsCard title="Tools" description="Switch a tool off to stop JARVIS from using it. Saved as soon as you change it.">
        {tools.isPending ? <SkeletonList rows={3} label="Loading tools" /> : null}
        {tools.isError ? <ErrorState message={tools.error.message} onRetry={() => void tools.refetch()} /> : null}
        {tools.data ? (
          <ul aria-label="Tools" className="flex flex-col gap-3">
            {tools.data.map((tool) => {
              const enabled = !automation.disabled_tools.includes(tool.name);
              return (
                <li key={tool.name} className="flex flex-col gap-1 rounded-lg border border-line bg-raised p-3">
                  <Switch
                    label={tool.title}
                    description={`${humanize(tool.category)}. Risk: ${tool.risk_level}.${tool.approval_required ? " Needs approval." : ""}`}
                    checked={enabled}
                    disabled={save.isPending}
                    onCheckedChange={(value) => toggleTool(tool.name, value)}
                  />
                  {!enabled ? <Badge tone="neutral">Switched off</Badge> : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </SettingsCard>
    </div>
  );
}

/**
 * The time zone for reminders and schedules. Saves only the top-level timezone key. Blank means UTC
 * and is sent as null. The list comes from the browser, and a text field is shown when it cannot list zones.
 */
function TimezoneCard({ timezone }: { timezone: string | null }) {
  const save = useSaveSettings();
  const zones = useMemo(() => supportedTimeZones(), []);
  const [draft, setDraft] = useState(timezoneToText(timezone));
  const [error, setError] = useState<string | null>(null);
  const dirty = timezoneIsDirty(draft, timezone);

  const onSave = (): void => {
    const parsed = timezoneFromInput(draft);
    if (parsed.error) {
      setError(parsed.error);
      return;
    }
    setError(null);
    save.mutate({ timezone: parsed.value });
  };

  // A saved zone the browser does not list is still shown, so that the field matches the server.
  const options = zones && draft.trim() !== "" && !zones.includes(draft.trim()) ? [draft.trim(), ...zones] : (zones ?? []);

  return (
    <SettingsCard title="Time zone" description="Used to read HH:MM reminders and schedules.">
      <Field label="Timezone for reminders and schedules" hint={zones ? "Blank means UTC." : "IANA name such as Europe/London. Blank means UTC."} error={error}>
        {(control) =>
          zones ? (
            <Select {...control} value={draft} invalid={Boolean(error)} onChange={(event) => setDraft(event.target.value)}>
              <option value="">{UTC_OPTION_LABEL}</option>
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          ) : (
            <Input
              {...control}
              type="text"
              autoComplete="off"
              spellCheck={false}
              maxLength={TIMEZONE_MAX_LENGTH}
              placeholder="Europe/London"
              invalid={Boolean(error)}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
          )
        }
      </Field>
      <SaveBar
        dirty={dirty}
        pending={save.isPending}
        error={save.isError ? save.error.message : null}
        onSave={onSave}
        onDiscard={() => {
          setDraft(timezoneToText(timezone));
          setError(null);
        }}
      />
    </SettingsCard>
  );
}
