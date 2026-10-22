import { useMemo, useRef, useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { SchedulePreview } from "./SchedulePreview";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input, Select, Textarea } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { ApiError } from "../../services/api";
import { useDevicesQuery, useToolsQuery } from "../../services/queries";
import { useCreateSchedule } from "./scheduleQueries";
import {
  KIND_LABEL,
  WEEKDAYS,
  argumentFields,
  browserTimeZone,
  defaultArgumentValues,
  defaultScheduleForm,
  previewSpec,
  validateScheduleForm,
  type ScheduleFormErrors,
  type ScheduleFormState,
  type ScheduleKind,
} from "./scheduleModel";
import { ToolArguments } from "./ToolArguments";

export interface ScheduleFormProps {
  onSaved: (name: string) => void;
  onCancel: () => void;
}

/**
 * Creates a schedule. Validation runs on submit, names every problem, and moves focus to the first
 * invalid control. Tool arguments come from the tool's JSON schema, for simple types only.
 */
export function ScheduleForm({ onSaved, onCancel }: ScheduleFormProps) {
  const tools = useToolsQuery();
  const devices = useDevicesQuery();
  const create = useCreateSchedule();
  const [form, setForm] = useState<ScheduleFormState>(() => defaultScheduleForm(Date.now(), browserTimeZone()));
  const [errors, setErrors] = useState<ScheduleFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  // The timing alone drives the preview, so the next runs show while the name or tool is still blank.
  const timingSpec = useMemo(() => previewSpec(form, Date.now()), [form]);
  const formRef = useRef<HTMLFormElement>(null);

  const toolList = useMemo(() => tools.data ?? [], [tools.data]);
  const deviceList = useMemo(() => devices.data ?? [], [devices.data]);
  const selectedTool = toolList.find((tool) => tool.name === form.toolName);
  const argumentSpec = useMemo(() => argumentFields(selectedTool?.parameters ?? {}), [selectedTool]);

  const patch = (partial: Partial<ScheduleFormState>): void => {
    setForm((current) => ({ ...current, ...partial }));
  };

  const chooseTool = (toolName: string): void => {
    const tool = toolList.find((item) => item.name === toolName);
    const spec = argumentFields(tool?.parameters ?? {});
    patch({ toolName, values: defaultArgumentValues(spec.fields), deviceId: "" });
  };

  const toggleWeekday = (value: number): void => {
    setForm((current) => ({
      ...current,
      weekdays: current.weekdays.includes(value) ? current.weekdays.filter((day) => day !== value) : [...current.weekdays, value],
    }));
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitError(null);
    const result = validateScheduleForm(form, { now: Date.now(), tools: toolList, devices: deviceList });
    if (!result.ok) {
      setErrors(result.errors);
      window.setTimeout(() => formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(), 0);
      return;
    }
    setErrors({});
    create.mutate(result.body, {
      onSuccess: (schedule) => onSaved(schedule.name),
      onError: (cause) => setSubmitError(cause instanceof ApiError ? cause.message : "The schedule could not be saved."),
    });
  };

  if (tools.isPending || devices.isPending) return <SkeletonList rows={4} label="Loading tools and devices" />;
  if (tools.isError) return <ErrorState message={tools.error.message} onRetry={() => void tools.refetch()} />;

  const errorCount = Object.keys(errors).length;

  return (
    <form ref={formRef} noValidate onSubmit={onSubmit} aria-label="New schedule" className="flex flex-col gap-5">
      {errorCount > 0 ? (
        <p role="alert" className="rounded-lg border border-danger/50 bg-danger/10 p-3 text-sm text-danger">
          {errorCount === 1 ? "One field needs attention." : `${errorCount} fields need attention.`}
        </p>
      ) : null}
      {submitError ? <ErrorState title="Schedule not saved" message={submitError} /> : null}

      <Field label="Name" error={errors.name ?? null} hint="Shown in the list and in task titles.">
        {(control) => <Input {...control} required invalid={Boolean(errors.name)} value={form.name} onChange={(e) => patch({ name: e.target.value })} />}
      </Field>

      <Field label="Description" hint="Optional." error={errors.description ?? null}>
        {(control) => (
          <Textarea
            {...control}
            invalid={Boolean(errors.description)}
            rows={2}
            value={form.description}
            onChange={(e) => patch({ description: e.target.value })}
          />
        )}
      </Field>

      <Field label="Tool" error={errors.tool ?? null}>
        {(control) => (
          <Select {...control} required invalid={Boolean(errors.tool)} value={form.toolName} onChange={(e) => chooseTool(e.target.value)}>
            <option value="">Choose a tool</option>
            {toolList.map((tool) => (
              <option key={tool.name} value={tool.name}>
                {tool.title}
                {tool.requires_device ? " (needs a device)" : ""}
              </option>
            ))}
          </Select>
        )}
      </Field>

      {selectedTool ? (
        <fieldset className="flex flex-col gap-3 rounded-lg border border-line p-4">
          <legend className="px-1 text-sm font-medium text-ink">Arguments</legend>
          <ToolArguments
            fields={argumentSpec.fields}
            unsupported={argumentSpec.unsupported}
            values={form.values}
            errors={errors}
            onChange={(name, value) => patch({ values: { ...form.values, [name]: value } })}
          />
          {errors.arguments ? (
            <p role="alert" className="text-xs font-medium text-danger">
              {errors.arguments}
            </p>
          ) : null}
        </fieldset>
      ) : null}

      <Field
        label="Device"
        hint={selectedTool?.requires_device ? "Required for this tool." : "Leave as server to run without a device."}
        error={errors.device ?? null}
      >
        {(control) => (
          <Select {...control} invalid={Boolean(errors.device)} value={form.deviceId} onChange={(e) => patch({ deviceId: e.target.value })}>
            <option value="">Server (no device)</option>
            {deviceList
              .filter((device) => device.pairing_status === "paired")
              .map((device) => (
                <option key={device.id} value={device.id}>
                  {device.name} ({device.connection_status})
                </option>
              ))}
          </Select>
        )}
      </Field>

      <Field label="Schedule">
        {(control) => (
          <Select
            {...control}
            value={form.kind}
            onChange={(e) => patch({ kind: e.target.value as ScheduleKind })}
          >
            {(Object.keys(KIND_LABEL) as ScheduleKind[]).map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABEL[kind]}
              </option>
            ))}
          </Select>
        )}
      </Field>

      {form.kind === "once" ? (
        <Field label="Date and time" hint="Your local time. It must be in the future." error={errors.runAt ?? null}>
          {(control) => (
            <Input
              {...control}
              type="datetime-local"
              required
              invalid={Boolean(errors.runAt)}
              value={form.runAt}
              onChange={(e) => patch({ runAt: e.target.value })}
            />
          )}
        </Field>
      ) : (
        <Field label="Time of day" hint="24-hour time, HH:MM, in the time zone below." error={errors.timeOfDay ?? null}>
          {(control) => (
            <Input
              {...control}
              type="time"
              required
              invalid={Boolean(errors.timeOfDay)}
              value={form.timeOfDay}
              onChange={(e) => patch({ timeOfDay: e.target.value })}
            />
          )}
        </Field>
      )}

      {form.kind === "weekly" ? (
        <fieldset className="flex flex-col gap-2" aria-describedby={errors.weekdays ? "weekday-error" : undefined}>
          <legend className="text-sm font-medium text-ink">Weekdays</legend>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((day) => {
              const checked = form.weekdays.includes(day.value);
              return (
                <label
                  key={day.value}
                  className={
                    checked
                      ? "inline-flex cursor-pointer items-center gap-2 rounded-lg border border-accent bg-raised px-3 py-1.5 text-sm text-ink"
                      : "inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-sm text-muted hover:text-ink"
                  }
                >
                  <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={checked} onChange={() => toggleWeekday(day.value)} />
                  {day.long}
                </label>
              );
            })}
          </div>
          {errors.weekdays ? (
            <p id="weekday-error" role="alert" className="text-xs font-medium text-danger">
              {errors.weekdays}
            </p>
          ) : null}
        </fieldset>
      ) : null}

      <Field label="Time zone" hint="Defaults to this browser's time zone. Daily and weekly times use it." error={errors.timezone ?? null}>
        {(control) => (
          <Input {...control} required invalid={Boolean(errors.timezone)} value={form.timezone} onChange={(e) => patch({ timezone: e.target.value })} />
        )}
      </Field>

      <SchedulePreview spec={timingSpec} />

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Attempts" hint="1 means no retry." error={errors.maxAttempts ?? null}>
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              max={5}
              invalid={Boolean(errors.maxAttempts)}
              value={form.maxAttempts}
              onChange={(e) => patch({ maxAttempts: e.target.value })}
            />
          )}
        </Field>
        <Field label="Backoff (seconds)" hint="Wait before each retry." error={errors.backoffSeconds ?? null}>
          {(control) => (
            <Input
              {...control}
              type="number"
              min={0}
              max={86400}
              invalid={Boolean(errors.backoffSeconds)}
              value={form.backoffSeconds}
              onChange={(e) => patch({ backoffSeconds: e.target.value })}
            />
          )}
        </Field>
      </div>

      <p className="text-xs text-muted">
        Device tools still create a task that waits for approval at each run, unless the device permission is set to allow and the tool is low
        risk.
      </p>

      <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Create schedule
        </Button>
      </div>
    </form>
  );
}
