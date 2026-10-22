/** Small formatting helpers shared by the pages. All functions are pure except where noted. */

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** "5 minutes ago", "in 2 hours". `now` is injectable for tests. */
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "Never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Unknown";
  const seconds = Math.round((then - now) / 1000);
  const abs = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 60) return formatter.format(seconds, "second");
  if (abs < 3600) return formatter.format(Math.round(seconds / 60), "minute");
  if (abs < 86_400) return formatter.format(Math.round(seconds / 3600), "hour");
  return formatter.format(Math.round(seconds / 86_400), "day");
}

/** Whole seconds left until the given time, never negative. */
export function secondsUntil(iso: string, now: number = Date.now()): number {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 0;
  return Math.max(0, Math.floor((then - now) / 1000));
}

export function formatCountdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Unavailable";
  return value.toLocaleString();
}

export function humanize(value: string): string {
  const spaced = value.replace(/[_.-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Returns a title for a new conversation from the first words of the first message. */
export function titleFromMessage(content: string, maxLength = 60): string {
  const collapsed = content.replace(/\s+/g, " ").trim();
  if (!collapsed) return "New conversation";
  return collapsed.length > maxLength ? `${collapsed.slice(0, maxLength - 1)}…` : collapsed;
}
