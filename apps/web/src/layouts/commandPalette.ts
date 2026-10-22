/**
 * The command palette's commands, filter and keyboard rules. Pure, so each rule can be tested. Every
 * command is a route that already exists; a command never performs an action on the server itself.
 */

import type { SearchResult } from "../types/api";
import type { NavItem } from "./nav";

export type CommandGroup = "Recent" | "Go to" | "Actions" | "Found";

/** How many conversations the palette offers before anything is typed. */
export const RECENT_LIMIT = 5;

export interface PaletteCommand {
  id: string;
  group: CommandGroup;
  label: string;
  /** Extra words that also match, such as "microphone" for the voice settings. */
  keywords: string;
  path: string;
}

/** Shortcuts to existing screens and their query parameters. */
export const ACTION_COMMANDS: readonly PaletteCommand[] = [
  { id: "action.new-chat", group: "Actions", label: "New chat", keywords: "conversation message write", path: "/chat" },
  { id: "action.new-task", group: "Actions", label: "New task", keywords: "create action run", path: "/tasks?create=1" },
  { id: "action.pair-device", group: "Actions", label: "Pair a desktop agent", keywords: "device code connect laptop", path: "/devices?pair=1" },
  { id: "action.approvals", group: "Actions", label: "Review approvals", keywords: "approve deny waiting", path: "/tasks?status=awaiting_approval" },
  { id: "action.voice-settings", group: "Actions", label: "Voice settings", keywords: "microphone speech speak", path: "/settings?tab=voice" },
  { id: "action.notification-settings", group: "Actions", label: "Notification settings", keywords: "pop-ups alerts quiet hours", path: "/settings?tab=notifications" },
  { id: "action.ai-settings", group: "Actions", label: "AI settings", keywords: "provider model temperature daily limit calls", path: "/settings?tab=ai" },
  { id: "action.appearance", group: "Actions", label: "Theme and appearance", keywords: "dark light high contrast colour color", path: "/settings?tab=appearance" },
  { id: "action.privacy", group: "Actions", label: "Privacy and my data", keywords: "download export delete clear conversations", path: "/settings?tab=privacy" },
  { id: "action.help-links", group: "Actions", label: "Chat from a link", keywords: "prompt deep link shortcut iphone", path: "/help" },
];

/** Every top-level page, then the actions. */
export function buildCommands(nav: readonly NavItem[]): PaletteCommand[] {
  const pages: PaletteCommand[] = nav.map((item) => ({
    id: `go.${item.id}`,
    group: "Go to",
    label: item.label,
    keywords: item.id,
    path: item.path,
  }));
  return [...pages, ...ACTION_COMMANDS];
}

/**
 * The conversations offered before anything is typed. The list arrives with pinned conversations first, then the
 * most recently updated, so the first few are the ones the person is most likely to want.
 */
export function recentCommands(conversations: readonly { id: string; title: string }[] | undefined): PaletteCommand[] {
  return (conversations ?? []).slice(0, RECENT_LIMIT).map((item) => ({
    id: `recent.chat.${item.id}`,
    group: "Recent" as const,
    label: item.title,
    keywords: "conversation recent chat",
    path: `/chat/${item.id}`,
  }));
}

/** Search hits as commands. A conversation or task opens its page; a memory opens the Memory page, where it can be found. */
export function searchCommands(result: SearchResult | undefined): PaletteCommand[] {
  if (!result) return [];
  return [
    ...result.conversations.map((item) => ({
      id: `found.chat.${item.id}`,
      group: "Found" as const,
      label: item.title,
      keywords: item.match === "title" ? "conversation" : "conversation message",
      path: `/chat/${item.id}`,
    })),
    ...result.tasks.map((item) => ({
      id: `found.task.${item.id}`,
      group: "Found" as const,
      label: item.title,
      keywords: `task ${item.tool_name}`,
      path: `/tasks/${item.id}`,
    })),
    ...result.memories.map((item) => ({
      id: `found.memory.${item.id}`,
      group: "Found" as const,
      label: item.excerpt,
      keywords: `memory ${item.category}`,
      path: "/memory",
    })),
  ];
}

/** Commands whose label, keywords or group contain every word of the query, ignoring case. An empty query keeps all. */
export function filterCommands(commands: readonly PaletteCommand[], query: string): PaletteCommand[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter((word) => word.length > 0);
  if (words.length === 0) return [...commands];
  return commands.filter((command) => {
    const haystack = `${command.label} ${command.keywords} ${command.group}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

/** Moves the highlight by one step, wrapping at both ends. With no items the index is 0. */
export function stepIndex(current: number, delta: 1 | -1, count: number): number {
  if (count <= 0) return 0;
  return (((current + delta) % count) + count) % count;
}

/** Ctrl+K on Windows and Linux, Cmd+K on macOS. */
export function isPaletteShortcut(event: { key: string; ctrlKey: boolean; metaKey: boolean }): boolean {
  return (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
}
