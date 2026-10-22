import type { IconName } from "../components/ui/Icon";

export interface NavItem {
  /** Stable id. Also the value stored in settings.appearance.sidebar_hidden. */
  id: string;
  label: string;
  path: string;
  icon: IconName;
}

/** Every top-level destination, in the default order. Routes live in routes.tsx. */
export const NAV_ITEMS: readonly NavItem[] = [
  { id: "overview", label: "Overview", path: "/overview", icon: "overview" },
  { id: "chat", label: "AI Chat", path: "/chat", icon: "chat" },
  { id: "voice", label: "Voice", path: "/voice", icon: "voice" },
  { id: "tasks", label: "Tasks", path: "/tasks", icon: "tasks" },
  { id: "devices", label: "Devices", path: "/devices", icon: "devices" },
  { id: "models", label: "AI Models", path: "/models", icon: "models" },
  { id: "schedule", label: "Schedule", path: "/schedule", icon: "schedule" },
  { id: "automation", label: "Automation", path: "/automation", icon: "automation" },
  { id: "explore", label: "Explore", path: "/explore", icon: "explore" },
  { id: "integrations", label: "Integrations", path: "/integrations", icon: "integrations" },
  { id: "memory", label: "Memory", path: "/memory", icon: "memory" },
  { id: "files", label: "Files", path: "/files", icon: "files" },
  { id: "notifications", label: "Notifications", path: "/notifications", icon: "notifications" },
  { id: "activity", label: "Activity", path: "/activity", icon: "activity" },
  { id: "analytics", label: "Analytics", path: "/analytics", icon: "analytics" },
  { id: "developer", label: "Developer", path: "/developer", icon: "developer" },
  { id: "settings", label: "Settings", path: "/settings", icon: "settings" },
  { id: "help", label: "Help", path: "/help", icon: "help" },
];

/**
 * Items in the viewer's order. Ids missing from `order` keep their default position at the end.
 * Unknown ids are ignored. Pure, so it can be tested.
 */
export function orderNavItems(order: readonly string[], items: readonly NavItem[] = NAV_ITEMS): NavItem[] {
  const byId = new Map(items.map((item) => [item.id, item] as const));
  const seen = new Set<string>();
  const ordered: NavItem[] = [];
  for (const id of order) {
    const item = byId.get(id);
    if (item && !seen.has(id)) {
      ordered.push(item);
      seen.add(id);
    }
  }
  for (const item of items) {
    if (!seen.has(item.id)) ordered.push(item);
  }
  return ordered;
}

/** Moves one item up or down. Returns the same array when the move is not possible. */
export function moveNavItem(order: readonly string[], id: string, direction: -1 | 1): string[] {
  const index = order.indexOf(id);
  const target = index + direction;
  if (index === -1 || target < 0 || target >= order.length) return [...order];
  const next = [...order];
  const [moved] = next.splice(index, 1);
  if (moved === undefined) return [...order];
  next.splice(target, 0, moved);
  return next;
}

/** Visible items: ordered, minus those the viewer hid. Hidden routes still work by URL. */
export function visibleNavItems(order: readonly string[], hidden: readonly string[], items: readonly NavItem[] = NAV_ITEMS): NavItem[] {
  return orderNavItems(order, items).filter((item) => !hidden.includes(item.id));
}
