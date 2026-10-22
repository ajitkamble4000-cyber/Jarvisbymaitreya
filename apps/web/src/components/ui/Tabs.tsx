import { useRef, type KeyboardEvent } from "react";
import { cn } from "./cn";

export interface TabItem {
  id: string;
  label: string;
  count?: number;
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  /** Accessible name for the tablist. */
  label: string;
  className?: string;
}

/** Tabs with roving focus: arrow keys move between tabs, Home and End jump to the ends. */
export function Tabs({ items, value, onChange, label, className }: TabsProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const focusIndex = (index: number): void => {
    const next = items[index];
    if (!next) return;
    onChange(next.id);
    refs.current[index]?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    if (event.key === "ArrowRight") focusIndex((index + 1) % items.length);
    else if (event.key === "ArrowLeft") focusIndex((index - 1 + items.length) % items.length);
    else if (event.key === "Home") focusIndex(0);
    else if (event.key === "End") focusIndex(items.length - 1);
    else return;
    event.preventDefault();
  };

  return (
    <div role="tablist" aria-label={label} className={cn("flex gap-1 overflow-x-auto rounded-lg border border-line bg-surface p-1", className)}>
      {items.map((item, index) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "inline-flex shrink-0 items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition",
              selected ? "bg-raised text-ink ring-1 ring-accent" : "text-muted hover:text-ink",
            )}
          >
            {item.label}
            {item.count !== undefined ? (
              <span className={cn("rounded-full px-1.5 text-xs tabular-nums", selected ? "bg-accent/20 text-ink" : "bg-raised text-muted")}>
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
