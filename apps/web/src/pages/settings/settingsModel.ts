/**
 * Settings helpers: change detection (so that a save sends only the keys that changed), numeric
 * parsing and the validation rules that mirror docs/api.md. Pure, so each rule can be tested.
 */

export const SETTINGS_TABS = [
  { id: "ai", label: "AI" },
  { id: "voice", label: "Voice" },
  { id: "appearance", label: "Appearance" },
  { id: "automation", label: "Automation" },
  { id: "notifications", label: "Notifications" },
  { id: "memory", label: "Memory" },
  { id: "security", label: "Security" },
  { id: "privacy", label: "Privacy" },
  { id: "users", label: "Users" },
] as const;

export type SettingsTabId = (typeof SETTINGS_TABS)[number]["id"];

/** Tabs that only the owner sees. The server enforces the same rule on /users. */
const OWNER_ONLY_TABS: readonly SettingsTabId[] = ["users"];

export function parseSettingsTab(value: string | null): SettingsTabId {
  return SETTINGS_TABS.some((tab) => tab.id === value) ? (value as SettingsTabId) : "ai";
}

/** The tabs a viewer may see. A member never sees the owner-only tabs. Pure. */
export function settingsTabsFor(isOwner: boolean): ReadonlyArray<(typeof SETTINGS_TABS)[number]> {
  return SETTINGS_TABS.filter((tab) => isOwner || !OWNER_ONLY_TABS.includes(tab.id));
}

/** A tab the viewer may not open falls back to the first tab, so a member's URL never shows the owner panel. */
export function effectiveSettingsTab(tab: SettingsTabId, isOwner: boolean): SettingsTabId {
  return !isOwner && OWNER_ONLY_TABS.includes(tab) ? "ai" : tab;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b) || (typeof a === "object" && a !== null) || (typeof b === "object" && b !== null)) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  return a === b;
}

/**
 * Returns only the keys of `draft` whose values differ from `server`. A save sends this object and
 * nothing else, so untouched settings are never overwritten.
 */
export function changedKeys<T extends object>(server: T, draft: T): Partial<T> {
  const changes: Partial<T> = {};
  for (const key of Object.keys(draft) as Array<keyof T>) {
    if (!sameValue(server[key], draft[key])) changes[key] = draft[key];
  }
  return changes;
}

export type FieldErrors = Record<string, string>;

/** Parses a number field. Blank, non-numeric, or out-of-range input returns an error message. */
export function parseBoundedNumber(text: string, min: number, max: number, label: string): { value: number | null; error: string | null } {
  const trimmed = text.trim();
  const value = Number(trimmed);
  if (trimmed === "" || !Number.isFinite(value)) return { value: null, error: `Enter a number for ${label}.` };
  if (value < min || value > max) return { value: null, error: `Enter a ${label} from ${min} to ${max}.` };
  return { value, error: null };
}

export function parseBoundedInteger(text: string, min: number, max: number, label: string): { value: number | null; error: string | null } {
  const parsed = parseBoundedNumber(text, min, max, label);
  if (parsed.error) return parsed;
  if (!Number.isInteger(parsed.value)) return { value: null, error: `Enter a whole number for ${label}.` };
  return parsed;
}

export const SYSTEM_INSTRUCTIONS_MAX = 4000;
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
const LANGUAGE_TAG = /^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;

export function isHexColor(value: string): boolean {
  return HEX_COLOR.test(value);
}

export function isLanguageTag(value: string): boolean {
  return LANGUAGE_TAG.test(value);
}

/** Validates the editable AI fields. The fallback must differ from the active provider. */
export function validateAiFields(input: {
  temperature: string;
  maxOutputTokens: string;
  systemInstructions: string;
  activeProvider: string;
  fallbackProvider: string;
}): FieldErrors {
  const errors: FieldErrors = {};
  const temperature = parseBoundedNumber(input.temperature, 0, 2, "temperature");
  if (temperature.error) errors.temperature = temperature.error;
  const tokens = parseBoundedInteger(input.maxOutputTokens, 64, 8192, "maximum output tokens");
  if (tokens.error) errors.maxOutputTokens = tokens.error;
  if (input.systemInstructions.length > SYSTEM_INSTRUCTIONS_MAX) {
    errors.systemInstructions = `Use ${SYSTEM_INSTRUCTIONS_MAX} characters or fewer.`;
  }
  if (input.fallbackProvider && input.fallbackProvider === input.activeProvider) {
    errors.fallbackProvider = "The fallback must be a different provider from the active one.";
  }
  return errors;
}

export function validateVoiceFields(input: { language: string; speakingRate: number; pitch: number }): FieldErrors {
  const errors: FieldErrors = {};
  if (!isLanguageTag(input.language.trim())) errors.language = "Use a language tag such as en-US.";
  if (input.speakingRate < 0.5 || input.speakingRate > 2) errors.speakingRate = "Speaking rate must be from 0.5 to 2.";
  if (input.pitch < 0.5 || input.pitch > 2) errors.pitch = "Pitch must be from 0.5 to 2.";
  return errors;
}

/** The daily call limit. Blank means no limit (null). Otherwise a whole number from 1 to 10000. */
export function parseCallLimit(text: string): { value: number | null; error: string | null } {
  if (text.trim() === "") return { value: null, error: null };
  return parseBoundedInteger(text, 1, 10000, "daily call limit");
}

/**
 * The sentence under the daily call limit: what has been used today and when the count starts again.
 * The server counts provider calls, so a refused reply is never part of the number.
 */
export function callsTodayText(today: { calls: number; limit: number | null }): string {
  const noun = (count: number): string => (count === 1 ? "AI call" : "AI calls");
  if (today.limit === null) return `Used today: ${today.calls} ${noun(today.calls)}. No daily limit is set.`;
  const scope = `Used today: ${today.calls} of ${today.limit} ${noun(today.limit)}.`;
  if (today.calls >= today.limit) return `${scope} The limit is reached. Replies resume after midnight UTC.`;
  return `${scope} The count resets at midnight UTC.`;
}

export function validateRetention(text: string): { value: number | null; error: string | null } {
  if (text.trim() === "") return { value: null, error: null };
  return parseBoundedInteger(text, 1, 3650, "retention in days");
}

/** Placeholder text for a field that the backend stores but does not apply yet. */
export const NOT_IMPLEMENTED = "Not implemented yet";
