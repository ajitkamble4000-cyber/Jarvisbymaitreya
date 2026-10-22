import { motion } from "framer-motion";
import { useReducedMotion } from "../../hooks/useReducedMotion";
import type { AnimationIntensity, OrbStyle } from "../../types/api";
import { cn } from "../ui/cn";

export type OrbState =
  | "idle"
  | "listening"
  | "processing"
  | "speaking"
  | "executing"
  | "awaiting_approval"
  | "disconnected"
  | "error";

export type OrbTone = "accent" | "info" | "success" | "warning" | "danger" | "muted";
export type OrbMotion = "breathe" | "ripple" | "spin" | "wave" | "pulse" | "none";

export interface OrbVisual {
  /** Text shown beside the orb and read by screen readers. Status is never conveyed by colour alone. */
  label: string;
  tone: OrbTone;
  motion: OrbMotion;
}

/** Maps each assistant state to its label, colour family and motion. Pure, so it can be tested. */
export function orbVisual(state: OrbState): OrbVisual {
  switch (state) {
    case "idle":
      return { label: "Ready", tone: "accent", motion: "breathe" };
    case "listening":
      return { label: "Listening", tone: "accent", motion: "ripple" };
    case "processing":
      return { label: "Thinking", tone: "info", motion: "spin" };
    case "speaking":
      return { label: "Speaking", tone: "success", motion: "wave" };
    case "executing":
      return { label: "Working on a task", tone: "info", motion: "spin" };
    case "awaiting_approval":
      return { label: "Waiting for your approval", tone: "warning", motion: "pulse" };
    case "disconnected":
      return { label: "Disconnected from JARVIS", tone: "muted", motion: "none" };
    case "error":
      return { label: "Something went wrong", tone: "danger", motion: "none" };
    default:
      return { label: "Ready", tone: "accent", motion: "breathe" };
  }
}

const RING: Record<OrbTone, string> = {
  accent: "border-accent",
  info: "border-info",
  success: "border-success",
  warning: "border-warning",
  danger: "border-danger",
  muted: "border-muted",
};

const FILL: Record<OrbTone, string> = {
  accent: "bg-accent/70",
  info: "bg-info/70",
  success: "bg-success/70",
  warning: "bg-warning/70",
  danger: "bg-danger/70",
  muted: "bg-muted/40",
};

export interface AIOrbProps {
  state: OrbState;
  style?: OrbStyle;
  intensity?: AnimationIntensity;
  showLabel?: boolean;
  className?: string;
}

/**
 * The central status orb. Motion stops when reduced motion applies (the setting or the OS
 * preference) or when the animation intensity is "off". The label always carries the state.
 */
export function AIOrb({ state, style = "orb", intensity = "normal", showLabel = true, className }: AIOrbProps) {
  const reduced = useReducedMotion();
  const visual = orbVisual(state);
  const animate = !reduced && intensity !== "off" && visual.motion !== "none";
  const amplitude = intensity === "high" ? 0.14 : intensity === "low" ? 0.04 : 0.08;
  const period = visual.motion === "spin" ? 3 : visual.motion === "ripple" ? 1.4 : 2.6;

  return (
    <div className={cn("flex flex-col items-center gap-3", className)}>
      <div role="img" aria-label={`Assistant status: ${visual.label}`} className="relative flex size-32 items-center justify-center">
        {visual.motion === "ripple" && animate ? (
          <motion.span
            aria-hidden="true"
            className={cn("absolute inset-0 rounded-full border", RING[visual.tone])}
            animate={{ scale: [0.8, 1.25], opacity: [0.7, 0] }}
            transition={{ repeat: Infinity, duration: period, ease: "easeOut" }}
          />
        ) : null}
        {visual.motion === "spin" && animate ? (
          <motion.span
            aria-hidden="true"
            className={cn("absolute inset-2 rounded-full border-2 border-dashed", RING[visual.tone])}
            animate={{ rotate: 360 }}
            transition={{ repeat: Infinity, duration: period, ease: "linear" }}
          />
        ) : null}
        <motion.span
          aria-hidden="true"
          className={cn(
            "relative size-20 rounded-full",
            style === "ring" ? cn("border-2 bg-transparent", RING[visual.tone]) : style === "minimal" ? cn("border", RING[visual.tone]) : cn(FILL[visual.tone], "shadow-lg"),
          )}
          animate={animate && (visual.motion === "breathe" || visual.motion === "wave" || visual.motion === "pulse") ? { scale: [1, 1 + amplitude, 1] } : undefined}
          transition={{ repeat: Infinity, duration: period, ease: "easeInOut" }}
        />
      </div>
      {showLabel ? <p className="text-sm font-medium text-ink">{visual.label}</p> : null}
    </div>
  );
}
