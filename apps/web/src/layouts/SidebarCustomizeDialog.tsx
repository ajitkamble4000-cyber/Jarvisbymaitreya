import { useMemo } from "react";
import { Dialog } from "../components/ui/Dialog";
import { Icon } from "../components/ui/Icon";
import { IconButton } from "../components/ui/IconButton";
import { Switch } from "../components/ui/Switch";
import { moveNavItem, orderNavItems, type NavItem } from "./nav";
import { uiStore, useUiStore } from "../stores/ui-store";

export interface SidebarCustomizeDialogProps {
  open: boolean;
  onClose: () => void;
  items: readonly NavItem[];
}

/**
 * Lets the viewer hide navigation items and move them up or down. Visibility is saved to the
 * settings API (appearance.sidebar_hidden). The order is kept on this device only.
 */
export function SidebarCustomizeDialog({ open, onClose, items }: SidebarCustomizeDialogProps) {
  const order = useUiStore((state) => state.sidebarOrder);
  const hidden = useUiStore((state) => state.hiddenNav);
  const ordered = useMemo(() => orderNavItems(order, items), [order, items]);
  const orderIds = ordered.map((item) => item.id);
  const visibleCount = ordered.filter((item) => !hidden.includes(item.id)).length;

  return (
    <Dialog open={open} onClose={onClose} title="Customise sidebar" description="Hide items you do not use and reorder the rest. Overview stays available by its own link.">
      <ul className="flex flex-col gap-2" aria-label="Sidebar items">
        {ordered.map((item, index) => {
          const isHidden = hidden.includes(item.id);
          const isLast = visibleCount <= 1 && !isHidden;
          return (
            <li key={item.id} className="flex items-center gap-3 rounded-lg border border-line bg-raised px-3 py-2">
              <Icon name={item.icon} className="size-4 text-muted" />
              <div className="min-w-0 flex-1">
                <Switch
                  label={item.label}
                  checked={!isHidden}
                  disabled={isLast}
                  onCheckedChange={(visible) => {
                    const nextHidden = visible ? hidden.filter((id) => id !== item.id) : [...hidden, item.id];
                    uiStore.setPreferences({ hiddenNav: nextHidden });
                  }}
                />
              </div>
              <div className="flex gap-1">
                <IconButton
                  label={`Move ${item.label} up`}
                  icon={<Icon name="chevronUp" className="size-4" />}
                  disabled={index === 0}
                  onClick={() => uiStore.setPreferences({ sidebarOrder: moveNavItem(orderIds, item.id, -1) })}
                />
                <IconButton
                  label={`Move ${item.label} down`}
                  icon={<Icon name="chevronDown" className="size-4" />}
                  disabled={index === ordered.length - 1}
                  onClick={() => uiStore.setPreferences({ sidebarOrder: moveNavItem(orderIds, item.id, 1) })}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
