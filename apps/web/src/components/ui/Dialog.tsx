import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "./cn";
import { IconButton } from "./IconButton";
import { Icon } from "./Icon";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** "center" for confirmations and forms, "bottom" for the mobile navigation sheet. */
  placement?: "center" | "bottom";
  size?: "sm" | "md" | "lg";
  footer?: ReactNode;
  children: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function focusableWithin(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((element) => !element.hasAttribute("hidden"));
}

/**
 * Modal dialog. Focus moves into the dialog on open and returns to the opener on close. Tab and
 * Shift+Tab cycle inside it, Escape closes it, and page scrolling is locked while it is open.
 */
export function Dialog({ open, onClose, title, description, placement = "center", size = "md", footer, children }: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = panelRef.current;
    const initial = focusableWithin(panel)[0] ?? panel;
    initial?.focus();

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusableWithin(panelRef.current);
      if (items.length === 0) {
        event.preventDefault();
        panelRef.current?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const inside = active instanceof HTMLElement && panelRef.current?.contains(active);
      if (!inside) {
        event.preventDefault();
        first?.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [open]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className={cn("fixed inset-0 z-50 flex", placement === "bottom" ? "items-end" : "items-center justify-center p-4")}>
      <div aria-hidden="true" className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative flex max-h-[90vh] w-full flex-col gap-4 overflow-hidden border border-line bg-surface p-5 shadow-2xl outline-none",
          placement === "bottom" ? "max-h-[85vh] rounded-t-2xl sm:max-w-md" : "rounded-card",
          placement === "center" && (size === "sm" ? "max-w-sm" : size === "lg" ? "max-w-3xl" : "max-w-lg"),
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold text-ink">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="mt-1 text-sm text-muted">
                {description}
              </p>
            ) : null}
          </div>
          <IconButton label="Close" onClick={onClose} icon={<Icon name="close" className="size-4" />} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
