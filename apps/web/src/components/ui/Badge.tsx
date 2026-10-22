import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const TONES: Record<BadgeTone, string> = {
  neutral: "border-line text-muted",
  accent: "border-accent/50 text-accent",
  success: "border-success/50 text-success",
  warning: "border-warning/50 text-warning",
  danger: "border-danger/50 text-danger",
  info: "border-info/50 text-info",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone], className)}
      {...rest}
    />
  );
}
