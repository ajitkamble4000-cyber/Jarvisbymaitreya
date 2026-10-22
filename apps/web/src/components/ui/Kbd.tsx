import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export function Kbd({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return <kbd className={cn("rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-xs text-muted", className)} {...rest} />;
}
