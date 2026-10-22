/**
 * Pure helpers for the Schedule page: form state, validation, the request body and the labels.
 * Nothing here touches the network, so every rule can be tested directly.
 */

import type { BadgeTone } from "../../components/ui/Badge";
import { formatDateTime, humanize } from "../../services/format";
import type { DeviceOut, ScheduleOut, ToolOut } from "../../types/api";

export type ScheduleKind = "once" | "daily" | "weekly";
export type ScheduleState = ScheduleOut["state"];
export type ScheduleTiming = ScheduleOut["schedule"];

/** Weekday numbers follow the backend: 0 = Monday .. 6 = Sunday. */
export const WEEKDAYS: ReadonlyArray<{ value: number; short: string; long: string }> = [
  { value: 0, short: "Mon", long: "Monday" },
  { value: 1, short: "Tue", long: "Tuesday" },
  { value: 2, short: "Wed", long: "Wednesday" },
  { value: 3, short: "Thu", long: "Thursday" },
  { value: 4, short: "Fri", long: "Friday" },
  { value: 5, short: "Sat", long: "Saturday" },
  { value: 6, short: "Sun", long: "Sunday" },
];

export const KIND_LABEL: Record<ScheduleKind, string> = {
  once: "Once",
  daily: "Daily",
  weekly: "Weekly",
};

export const STATE_LABEL: Record<ScheduleState, string> = {
  scheduled: "Scheduled",
  paused: "Paused",
  disabled: "Disabled",
  completed: "Completed",
  failing: "Failing",
};

export const STATE_TONE: Record<ScheduleState, BadgeTone> = {
  scheduled: "success",
  paused: "warning",
  disabled: "neutral",
  completed: "info",
  failing: "danger",
};

const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;
const MAX_NAME = 200;
const MAX_DESCRIPTION = 2000;
const MAX_ATTEMPTS = 5;
const MAX_BACKOFF_SECONDS = 86_400;

/** Raw values held by the form. Numbers stay strings until validation, as inputs produce strings. */
export interface ScheduleFormState {
  name: string;
  description: string;
  toolName: string;
  deviceId: string;
  values: Record<string, string | boolean>;
  kind: ScheduleKind;
  /** datetime-local value, for example 2026-10-20T09:00. Read in the browser's local time. */
  runAt: string;
  /** HH:MM, used by daily and weekly schedules. */
  timeOfDay: string;
  weekdays: number[];
  timezone: string;
  maxAttempts: string;
  backoffSeconds: string;
}

/** Field errors keyed by field name. Argument errors use the key `arg:<name>`. */
export type ScheduleFormErrors = Record<string, string>;

export interface ScheduleCreateBody {
  name: string;
  description: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  device_id?: string;
  schedule: {
    kind: ScheduleKind;
    run_at?: string;
    time_of_day?: string;
    weekdays?: number[];
    timezone: string;
  };
  retry: { max_attempts: number; backoff_seconds: number };
}

/**
 * The timing part of a schedule, for the preview. It needs only a valid time zone and a time (or a future
 * one-time date), so the preview can show runs while the name or tool is still being filled in. Null otherwise.
 */
export function previewSpec(form: ScheduleFormState, now: number): ScheduleCreateBody["schedule"] | null {
  const timezone = form.timezone.trim();
  if (!isValidTimeZone(timezone)) return null;
  if (form.kind === "once") {
    if (!form.runAt) return null;
    const when = new Date(form.runAt);
    if (Number.isNaN(when.getTime()) || when.getTime() <= now) return null;
    return { kind: "once", run_at: when.toISOString(), timezone };
  }
  if (!TIME_PATTERN.test(form.timeOfDay)) return null;
  if (form.kind === "daily") return { kind: "daily", time_of_day: form.timeOfDay, timezone };
  if (form.weekdays.length === 0) return null;
  return { kind: "weekly", time_of_day: form.timeOfDay, weekdays: [...form.weekdays].sort((a, b) => a - b), timezone };
}

export type ScheduleValidation = { ok: true; body: ScheduleCreateBody } | { ok: false; errors: ScheduleFormErrors };

export interface ValidationContext {
  /** Epoch milliseconds. Injected so that "in the future" is testable. */
  now: number;
  tools: readonly ToolOut[];
  devices: readonly DeviceOut[];
}

// ---------------------------------------------------------------- argument fields (simple JSON schema types only)

export type ArgumentType = "string" | "integer" | "number" | "boolean";

export interface ArgumentField {
  name: string;
  label: string;
  type: ArgumentType;
  required: boolean;
  description: string | null;
  enumValues: string[] | null;
}

export interface UnsupportedArgument {
  name: string;
  required: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Reads a tool's JSON schema and keeps only simple properties (string, integer, number, boolean,
 * and string enums). Anything else is reported as unsupported so the form can say so.
 */
export function argumentFields(parameters: Record<string, unknown>): { fields: ArgumentField[]; unsupported: UnsupportedArgument[] } {
  const properties = isRecord(parameters.properties) ? parameters.properties : {};
  const requiredList = Array.isArray(parameters.required) ? parameters.required : [];
  const required = new Set(requiredList.filter((item): item is string => typeof item === "string"));
  const fields: ArgumentField[] = [];
  const unsupported: UnsupportedArgument[] = [];

  for (const [name, schemaValue] of Object.entries(properties)) {
    const schema = isRecord(schemaValue) ? schemaValue : {};
    const isRequired = required.has(name);
    const type = typeof schema.type === "string" ? schema.type : null;
    const enumValues =
      Array.isArray(schema.enum) && schema.enum.every((item) => typeof item === "string") ? (schema.enum as string[]) : null;
    const description = typeof schema.description === "string" && schema.description ? schema.description : null;

    if (type === "string") {
      fields.push({ name, label: humanize(name), type: "string", required: isRequired, description, enumValues });
    } else if (type === "integer" || type === "number" || type === "boolean") {
      fields.push({ name, label: humanize(name), type, required: isRequired, description, enumValues: null });
    } else {
      unsupported.push({ name, required: isRequired });
    }
  }
  return { fields, unsupported };
}

/** Builds the arguments object from raw values. Optional empty fields are left out. */
export function buildArguments(
  fields: readonly ArgumentField[],
  values: Record<string, string | boolean>,
): { args: Record<string, unknown>; errors: ScheduleFormErrors } {
  const args: Record<string, unknown> = {};
  const errors: ScheduleFormErrors = {};

  for (const field of fields) {
    const key = `arg:${field.name}`;
    const raw = values[field.name];

    if (field.type === "boolean") {
      if (raw === true || field.required) args[field.name] = raw === true;
      continue;
    }

    const text = typeof raw === "string" ? raw.trim() : "";
    if (text === "") {
      if (field.required) errors[key] = `Enter a value for ${field.label}.`;
      continue;
    }

    if (field.type === "string") {
      if (field.enumValues && !field.enumValues.includes(text)) {
        errors[key] = `Choose one of: ${field.enumValues.join(", ")}.`;
      } else {
        args[field.name] = text;
      }
    } else if (field.type === "integer") {
      if (/^-?\d+$/.test(text)) args[field.name] = Number(text);
      else errors[key] = "Enter a whole number.";
    } else {
      const parsed = Number(text);
      if (Number.isFinite(parsed)) args[field.name] = parsed;
      else errors[key] = "Enter a number.";
    }
  }
  return { args, errors };
}

/** Default values for a tool's fields: booleans start unchecked, everything else empty. */
export function defaultArgumentValues(fields: readonly ArgumentField[]): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    values[field.name] = field.type === "boolean" ? false : "";
  }
  return values;
}

// ---------------------------------------------------------------- time zones and local times

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function isValidTimeZone(zone: string): boolean {
  if (!zone) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** Formats a Date as a datetime-local value in the browser's local time. */
export function toLocalInputValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function defaultScheduleForm(now: number, timezone: string): ScheduleFormState {
  return {
    name: "",
    description: "",
    toolName: "",
    deviceId: "",
    values: {},
    kind: "daily",
    runAt: toLocalInputValue(new Date(now + 60 * 60 * 1000)),
    timeOfDay: "09:00",
    weekdays: [],
    timezone,
    maxAttempts: "1",
    backoffSeconds: "300",
  };
}

function parseWhole(value: string, min: number, max: number): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const number = Number(trimmed);
  return number >= min && number <= max ? number : null;
}

// ---------------------------------------------------------------- validation

/**
 * Checks the whole form and builds the POST /schedules body when it is valid. Rules: a one-time
 * schedule needs a future date and time; daily and weekly need HH:MM; weekly needs at least one
 * weekday; the time zone must be a real IANA zone; retries stay within the backend limits.
 */
export function validateScheduleForm(form: ScheduleFormState, ctx: ValidationContext): ScheduleValidation {
  const errors: ScheduleFormErrors = {};

  const name = form.name.trim();
  if (!name) errors.name = "Enter a name for this schedule.";
  else if (name.length > MAX_NAME) errors.name = `Use ${MAX_NAME} characters or fewer.`;
  if (form.description.length > MAX_DESCRIPTION) errors.description = `Use ${MAX_DESCRIPTION} characters or fewer.`;

  const tool = ctx.tools.find((item) => item.name === form.toolName);
  if (!tool) errors.tool = "Choose a tool.";

  if (tool && tool.requires_device && !form.deviceId) errors.device = "Choose a device for this tool.";
  if (form.deviceId && !ctx.devices.some((device) => device.id === form.deviceId && device.pairing_status === "paired")) {
    errors.device = "Choose a device that is still paired.";
  }

  if (tool) {
    const { fields, unsupported } = argumentFields(tool.parameters);
    const built = buildArguments(fields, form.values);
    Object.assign(errors, built.errors);
    const blocking = unsupported.filter((item) => item.required).map((item) => item.name);
    if (blocking.length > 0) {
      errors.arguments = `This tool needs ${blocking.join(", ")}, which this form cannot enter. Run it from Tasks instead.`;
    }
  }

  const timezone = form.timezone.trim();
  if (!isValidTimeZone(timezone)) errors.timezone = "Enter a valid time zone, such as Europe/London.";

  let schedule: ScheduleCreateBody["schedule"] | null = null;
  if (form.kind === "once") {
    if (!form.runAt) {
      errors.runAt = "Enter a date and time.";
    } else {
      const when = new Date(form.runAt);
      if (Number.isNaN(when.getTime())) errors.runAt = "Enter a valid date and time.";
      else if (when.getTime() <= ctx.now) errors.runAt = "Choose a time in the future.";
      else schedule = { kind: "once", run_at: when.toISOString(), timezone };
    }
  } else {
    if (!TIME_PATTERN.test(form.timeOfDay)) errors.timeOfDay = "Enter a time as HH:MM.";
    if (form.kind === "weekly" && form.weekdays.length === 0) errors.weekdays = "Choose at least one weekday.";
    if (TIME_PATTERN.test(form.timeOfDay) && (form.kind === "daily" || form.weekdays.length > 0)) {
      schedule =
        form.kind === "daily"
          ? { kind: "daily", time_of_day: form.timeOfDay, timezone }
          : { kind: "weekly", time_of_day: form.timeOfDay, weekdays: [...form.weekdays].sort((a, b) => a - b), timezone };
    }
  }

  const attempts = parseWhole(form.maxAttempts, 1, MAX_ATTEMPTS);
  if (attempts === null) errors.maxAttempts = `Enter a whole number of attempts from 1 to ${MAX_ATTEMPTS}.`;
  const backoff = parseWhole(form.backoffSeconds, 0, MAX_BACKOFF_SECONDS);
  if (backoff === null) errors.backoffSeconds = `Enter a whole number of seconds from 0 to ${MAX_BACKOFF_SECONDS}.`;

  if (Object.keys(errors).length > 0 || !tool || !schedule || attempts === null || backoff === null) {
    return { ok: false, errors };
  }

  const { args } = buildArguments(argumentFields(tool.parameters).fields, form.values);
  const body: ScheduleCreateBody = {
    name,
    description: form.description.trim(),
    tool_name: tool.name,
    arguments: args,
    schedule,
    retry: { max_attempts: attempts, backoff_seconds: backoff },
  };
  if (form.deviceId) body.device_id = form.deviceId;
  return { ok: true, body };
}

// ---------------------------------------------------------------- display

export function describeSchedule(timing: ScheduleTiming): string {
  if (timing.kind === "once") return `Once at ${formatDateTime(timing.run_at)}`;
  const time = timing.time_of_day ?? "";
  if (timing.kind === "daily") return `Every day at ${time} (${timing.timezone})`;
  const days = (timing.weekdays ?? [])
    .map((value) => WEEKDAYS.find((item) => item.value === value)?.short ?? String(value))
    .join(", ");
  return `Every ${days || "week"} at ${time} (${timing.timezone})`;
}

/** Orders schedules by next run, earliest first. Schedules without a next run go last. */
export function sortByNextRun(items: readonly ScheduleOut[]): ScheduleOut[] {
  return [...items].sort((a, b) => {
    if (a.next_run_at && b.next_run_at) return a.next_run_at.localeCompare(b.next_run_at) || a.name.localeCompare(b.name);
    if (a.next_run_at) return -1;
    if (b.next_run_at) return 1;
    return a.name.localeCompare(b.name);
  });
}

/** Reads a run error that may be a message object or plain text. Returns null when there is none. */
export function runErrorText(error: Record<string, unknown> | null): string | null {
  if (!error) return null;
  if (typeof error.message === "string" && error.message) return error.message;
  if (typeof error.code === "string" && error.code) return `Error code: ${error.code}`;
  return "The run recorded an error.";
}

/** Pause and resume apply while a schedule is still running. Disabled and completed ones have no pause control. */
export function supportsPauseToggle(state: ScheduleState): boolean {
  return state === "scheduled" || state === "failing" || state === "paused";
}
