import { useId, type ReactNode } from "react";
import { cn } from "./cn";

export interface TooltipProps {
  content: string;
  className?: string;
  /** Receives the tooltip id. The trigger must pass it as aria-describedby. */
  children: (describedBy: string) => ReactNode;
}

/** Hint text shown on hover and focus. The trigger is responsible for aria-describedby. */
export function Tooltip({ content, className, children }: TooltipProps) {
  const id = useId();
  return (
    <span className={cn("group relative inline-flex", className)}>
      {children(id)}
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-40 mb-2 hidden w-max max-w-56 -translate-x-1/2 rounded-md border border-line bg-raised px-2 py-1 text-xs text-ink shadow group-focus-within:block group-hover:block"
      >
        {content}
      </span>
    </span>
  );
}
