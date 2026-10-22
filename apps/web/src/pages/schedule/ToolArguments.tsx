import { Field } from "../../components/ui/Field";
import { Input, Select } from "../../components/ui/Input";
import type { ArgumentField, ScheduleFormErrors, UnsupportedArgument } from "./scheduleModel";

export interface ToolArgumentsProps {
  fields: readonly ArgumentField[];
  unsupported: readonly UnsupportedArgument[];
  values: Record<string, string | boolean>;
  errors: ScheduleFormErrors;
  onChange: (name: string, value: string | boolean) => void;
}

/** Generates inputs from a tool's JSON schema. Only simple types are offered; see argumentFields. */
export function ToolArguments({ fields, unsupported, values, errors, onChange }: ToolArgumentsProps) {
  if (fields.length === 0 && unsupported.length === 0) {
    return <p className="text-sm text-muted">This tool takes no arguments.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {fields.map((field) => {
        const error = errors[`arg:${field.name}`] ?? null;
        if (field.type === "boolean") {
          const checked = values[field.name] === true;
          return (
            <div key={field.name} className="flex flex-col gap-1">
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(event) => onChange(field.name, event.target.checked)}
                  className="size-4 accent-[var(--accent)]"
                />
                {field.label}
              </label>
              {field.description ? <p className="text-xs text-muted">{field.description}</p> : null}
            </div>
          );
        }

        const raw = typeof values[field.name] === "string" ? (values[field.name] as string) : "";
        if (field.enumValues) {
          return (
            <Field key={field.name} label={field.label} hint={field.description ?? undefined} error={error}>
              {(control) => (
                <Select {...control} invalid={Boolean(error)} value={raw} onChange={(event) => onChange(field.name, event.target.value)}>
                  <option value="">{field.required ? "Choose…" : "Not set"}</option>
                  {field.enumValues?.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          );
        }

        const numeric = field.type === "integer" || field.type === "number";
        return (
          <Field key={field.name} label={field.required ? `${field.label} (required)` : field.label} hint={field.description ?? undefined} error={error}>
            {(control) => (
              <Input
                {...control}
                invalid={Boolean(error)}
                type={numeric ? "number" : "text"}
                step={field.type === "integer" ? 1 : "any"}
                value={raw}
                onChange={(event) => onChange(field.name, event.target.value)}
              />
            )}
          </Field>
        );
      })}
      {unsupported.length > 0 ? (
        <p role="note" className="text-xs text-muted">
          This form cannot enter {unsupported.map((item) => item.name).join(", ")} because {unsupported.length === 1 ? "it is" : "they are"} a
          complex value. Use the Tasks page for that.
        </p>
      ) : null}
    </div>
  );
}
