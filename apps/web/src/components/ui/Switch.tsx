import { useId } from "react";
import { cn } from "./cn";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
  className?: string;
}

/** A labelled on/off control using role="switch". The label is visible text. */
export function Switch({ checked, onCheckedChange, label, description, disabled = false, className }: SwitchProps) {
  const labelId = useId();
  const descriptionId = useId();
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        <span id={labelId} className="text-sm font-medium text-ink">
          {label}
        </span>
        {description ? (
          <p id={descriptionId} className="text-xs text-muted">
            {description}
          </p>
        ) : null}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={description ? descriptionId : undefined}
        disabled={disabled}
        onClick={() => onCheckedChange(!checked)}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition disabled:cursor-not-allowed disabled:opacity-50",
          checked ? "border-accent bg-accent" : "border-line bg-raised",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "inline-block size-4 rounded-full shadow transition-transform",
            checked ? "translate-x-6 bg-on-accent" : "translate-x-1 bg-muted",
          )}
        />
      </button>
    </div>
  );
}
