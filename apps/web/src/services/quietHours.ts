/**
 * Quiet hours for browser pop-ups. The window is "HH:MM" to "HH:MM" in the reminder time zone, or in this
 * browser's own zone when no reminder zone is set. A window whose start is later than its end runs overnight.
 * The end is exclusive: at the end time, pop-ups are allowed again. Pure apart from Intl, so each rule can be tested.
 */

const CLOCK = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

export interface QuietWindow {
  enabled: boolean;
  start: string | null;
  end: string | null;
}

/** "HH:MM" to minutes after midnight, or null when the text is not a clock time. */
export function parseClock(value: string | null | undefined): number | null {
  if (!value || !CLOCK.test(value)) return null;
  const [hours, minutes] = value.split(":").map(Number);
  return (hours ?? 0) * 60 + (minutes ?? 0);
}

/** The wall-clock time at `now` in `zone`. An unknown zone, or no zone, uses this browser's zone. */
export function clockAt(now: Date, zone: string | null): { hour: number; minute: number } {
  const options: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
  try {
    const parts = new Intl.DateTimeFormat("en-GB", zone ? { ...options, timeZone: zone } : options).formatToParts(now);
    const hour = Number(parts.find((part) => part.type === "hour")?.value);
    const minute = Number(parts.find((part) => part.type === "minute")?.value);
    if (Number.isInteger(hour) && Number.isInteger(minute)) return { hour: hour % 24, minute };
  } catch {
    // An unknown zone name falls through to the browser's own clock.
  }
  return { hour: now.getHours(), minute: now.getMinutes() };
}

/** True when `minutes` is inside the window. Start is inclusive, end is exclusive, and a start after the end wraps past midnight. */
export function isInWindow(minutes: number, start: number, end: number): boolean {
  if (start === end) return false;
  if (start < end) return minutes >= start && minutes < end;
  return minutes >= start || minutes < end;
}

/** Whether pop-ups are paused at `now`. Incomplete or disabled windows never pause anything. */
export function quietAt(window: QuietWindow, now: Date, zone: string | null): boolean {
  if (!window.enabled) return false;
  const start = parseClock(window.start);
  const end = parseClock(window.end);
  if (start === null || end === null) return false;
  const { hour, minute } = clockAt(now, zone);
  return isInWindow(hour * 60 + minute, start, end);
}
