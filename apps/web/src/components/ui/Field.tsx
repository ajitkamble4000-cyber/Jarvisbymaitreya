import { useId, type ReactNode } from "react";
import { cn } from "./cn";

export interface FieldControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
}

export interface FieldProps {
  label: string;
  hint?: string;
  error?: string | null;
  className?: string;
  /** Visually hide the label but keep it for assistive technology. */
  hideLabel?: boolean;
  /** Receives the id and aria attributes that the control must spread onto the input. */
  children: (control: FieldControlProps) => ReactNode;
}

/** Label, hint and error text wired to one control through ids. The error is announced politely. */
export function Field({ label, hint, error, className, hideLabel = false, children }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className={cn("text-sm font-medium text-ink", hideLabel && "sr-only")}>
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint ? (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="text-xs font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
