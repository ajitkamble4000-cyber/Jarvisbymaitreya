/**
 * Timezone helpers for the Automation section. Pure apart from the Intl calls, so each rule can be
 * tested. The server stores one IANA name for "HH:MM" reminders and schedules, or null for UTC.
 */

export const TIMEZONE_MAX_LENGTH = 64;

/** The label of the blank option. Blank is saved as null, because the API rejects an empty string. */
export const UTC_OPTION_LABEL = "UTC (no time zone set)";

/**
 * The browser's list of IANA zones, or null when the browser cannot list them. The caller then shows
 * a free-text field instead of a list.
 */
export function supportedTimeZones(): string[] | null {
  try {
    const intl = Intl as typeof Intl & { supportedValuesOf?: (key: "timeZone") => string[] };
    if (typeof intl.supportedValuesOf !== "function") return null;
    const zones = intl.supportedValuesOf("timeZone");
    return zones.length > 0 ? zones : null;
  } catch {
    return null;
  }
}

/** True when the browser knows the zone name. The API checks the name again when it is saved. */
export function isKnownTimeZone(name: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

/**
 * Turns the field text into the value to save. Blank means UTC and gives null. Anything else must be
 * an IANA name the browser recognises.
 */
export function timezoneFromInput(text: string): { value: string | null; error: string | null } {
  const trimmed = text.trim();
  if (trimmed === "") return { value: null, error: null };
  if (trimmed.length > TIMEZONE_MAX_LENGTH) return { value: null, error: `Use ${TIMEZONE_MAX_LENGTH} characters or fewer.` };
  if (!isKnownTimeZone(trimmed)) return { value: null, error: "Use an IANA time zone name such as Europe/London, or leave blank for UTC." };
  return { value: trimmed, error: null };
}

/** The text a field shows for a saved value. null (UTC) shows as blank. */
export function timezoneToText(saved: string | null): string {
  return saved ?? "";
}

/** True when the field text would change the saved value. Blank and null both mean UTC. */
export function timezoneIsDirty(text: string, saved: string | null): boolean {
  return text.trim() !== timezoneToText(saved);
}
