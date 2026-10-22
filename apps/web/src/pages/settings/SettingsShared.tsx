import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { ErrorState } from "../../components/ui/ErrorState";
import { Switch } from "../../components/ui/Switch";
import { NOT_IMPLEMENTED } from "./settingsModel";

/** A titled group of settings. */
export function SettingsCard({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <CardTitle>{title}</CardTitle>
        {description ? <p className="text-sm text-muted">{description}</p> : null}
      </div>
      {children}
    </Card>
  );
}

/** A switch that saves at once. It is disabled while any save runs, so two changes never race. */
export function ImmediateSwitch({
  label,
  description,
  checked,
  pending,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  pending: boolean;
  onChange: (value: boolean) => void;
}) {
  return <Switch label={label} description={description} checked={checked} disabled={pending} onCheckedChange={onChange} />;
}

/** A setting that is saved but that no screen reads yet. It shows the stored state as text and has no control. */
export function StoredOnlyRow({ label, value, detail }: { label: string; value: boolean; detail: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-sm font-medium text-ink">{label}</span>
      <span className="text-xs font-medium text-warning">Not applied yet</span>
      <span className="text-xs text-muted">
        Stored as {value ? "on" : "off"}. {detail}
      </span>
    </div>
  );
}

/** A row for a setting the backend stores but does not apply. It shows the state and never a control. */
export function NotImplementedRow({ label, detail }: { label: string; detail?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-sm font-medium text-ink">{label}</span>
      <span className="text-xs font-medium text-warning">{NOT_IMPLEMENTED}</span>
      {detail ? <span className="text-xs text-muted">{detail}</span> : null}
    </div>
  );
}

/** The save line for a form. Explains the state in text and keeps the button disabled until something changed. */
export function SaveBar({
  dirty,
  pending,
  error,
  onSave,
  onDiscard,
}: {
  dirty: boolean;
  pending: boolean;
  error: string | null;
  onSave: () => void;
  onDiscard?: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      {error ? <ErrorState title="Not saved" message={error} /> : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <p className="mr-auto text-xs text-muted" aria-live="polite">
          {pending ? "Saving…" : dirty ? "You have unsaved changes." : "No unsaved changes."}
        </p>
        {onDiscard ? (
          <Button variant="ghost" disabled={!dirty || pending} onClick={onDiscard}>
            Discard
          </Button>
        ) : null}
        <Button variant="primary" disabled={!dirty} loading={pending} onClick={onSave}>
          Save changes
        </Button>
      </div>
    </div>
  );
}

export function SectionLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="text-sm text-accent underline underline-offset-4">
      {children}
    </Link>
  );
}

/** Shows the first error from a field-error map beside the field, or nothing. */
export function FieldError({ message }: { message?: string }) {
  return message ? (
    <p role="alert" className="text-xs font-medium text-danger">
      {message}
    </p>
  ) : null;
}
