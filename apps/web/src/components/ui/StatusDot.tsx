import { cn } from "./cn";

export type StatusTone = "success" | "warning" | "danger" | "info" | "accent" | "muted";

const DOT: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  accent: "bg-accent",
  muted: "bg-muted",
};

export interface StatusDotProps {
  tone: StatusTone;
  /** Always rendered. Hidden visually only when showLabel is false, and never removed for screen readers. */
  label: string;
  showLabel?: boolean;
  pulse?: boolean;
  className?: string;
}

/** A status indicator. The text label carries the meaning, so colour is never the only signal. */
export function StatusDot({ tone, label, showLabel = true, pulse = false, className }: StatusDotProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm text-ink", className)}>
      <span aria-hidden="true" className={cn("size-2 shrink-0 rounded-full", DOT[tone], pulse && "animate-pulse")} />
      <span className={showLabel ? undefined : "sr-only"}>{label}</span>
    </span>
  );
}
