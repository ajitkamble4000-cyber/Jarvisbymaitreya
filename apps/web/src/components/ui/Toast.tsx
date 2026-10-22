import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "./cn";
import { IconButton } from "./IconButton";
import { Icon } from "./Icon";

export type ToastTone = "info" | "success" | "error";

export interface ToastInput {
  title: string;
  description?: string;
  tone?: ToastTone;
  /** Milliseconds before the toast closes itself. Errors stay until dismissed. */
  durationMs?: number;
}

export interface ToastItem extends ToastInput {
  id: number;
  tone: ToastTone;
}

interface ToastContextValue {
  toast: (input: ToastInput) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TONE_CLASS: Record<ToastTone, string> = {
  info: "border-info/50",
  success: "border-success/50",
  error: "border-danger/60",
};

/** Hosts the toast region. Mount once, near the root. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const toast = useCallback(
    (input: ToastInput): number => {
      const id = nextId.current;
      nextId.current += 1;
      const tone = input.tone ?? "info";
      setItems((current) => [...current.slice(-4), { ...input, id, tone }]);
      if (tone !== "error") {
        window.setTimeout(() => dismiss(id), input.durationMs ?? 5000);
      }
      return id;
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div aria-live="polite" role="region" aria-label="Notifications" className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4 sm:items-end sm:px-6">
        {items.map((item) => (
          <div
            key={item.id}
            role={item.tone === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border bg-surface p-3 text-sm shadow-xl",
              TONE_CLASS[item.tone],
            )}
          >
            <div className="min-w-0 flex-1">
              <p className="font-medium text-ink">{item.title}</p>
              {item.description ? <p className="mt-0.5 text-muted">{item.description}</p> : null}
            </div>
            <IconButton label="Dismiss notification" icon={<Icon name="x" className="size-4" />} onClick={() => dismiss(item.id)} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Returns toast() and dismiss(). Must be used inside ToastProvider. */
export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) {
    throw new Error("useToast must be used inside ToastProvider");
  }
  return value;
}
