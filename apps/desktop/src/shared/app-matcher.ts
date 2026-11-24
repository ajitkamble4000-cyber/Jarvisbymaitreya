/**
 * Matches a spoken app name ("open Whisper Flow") to an app installed on this device. The list of installed apps
 * comes from the device itself; the backend sends only the words the person said.
 *
 * Order: built-in aliases for names speech recognition often gets wrong, then exact name, then a name that starts
 * with or contains the words, then a close spelling. Anything weaker is not launched: the caller reports the closest
 * names instead, so the person can say which one they meant.
 */

export interface InstalledApp {
  /** Display name from the Start menu, for example "Wispr Flow". */
  name: string;
  /** Windows AppUserModelID from Get-StartApps. It never leaves the device. */
  appId: string;
}

export type MatchResult =
  | { kind: "match"; app: InstalledApp; how: "alias" | "exact" | "prefix" | "contains" | "close" }
  | { kind: "ambiguous"; candidates: InstalledApp[] }
  | { kind: "none"; suggestions: InstalledApp[] };

/** Spoken or misheard name -> the name the app has in the Start menu. Keys and values are normalised. */
export const APP_ALIASES: Readonly<Record<string, string>> = {
  "whisper flow": "wispr flow",
  "whisper": "wispr flow",
  "wisper flow": "wispr flow",
  "wispr": "wispr flow",
  "vs code": "visual studio code",
  "vscode": "visual studio code",
  "v s code": "visual studio code",
  "code editor": "visual studio code",
  "chrome": "google chrome",
  "browser": "google chrome",
  "word": "word",
  "ms word": "word",
  "microsoft word": "word",
  "excel": "excel",
  "powerpoint": "powerpoint",
  "calculator": "calculator",
  "calc": "calculator",
  "notepad": "notepad",
  "file explorer": "file explorer",
  "explorer": "file explorer",
  "settings": "settings",
  "spotify music": "spotify",
  "spoti fly": "spotify",
};

const FILLER = new Set(["open", "launch", "start", "run", "the", "app", "application", "please", "my"]);

export function normaliseAppName(value: string): string {
  const words = value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9+#]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter((word) => word.length > 0 && !FILLER.has(word));
  return words.join(" ");
}

function levenshtein(a: string, b: string): number {
  if (a === b) {
    return 0;
  }
  const previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    let diagonal = previous[0] ?? 0;
    previous[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const above = previous[j] ?? 0;
      const left = previous[j - 1] ?? 0;
      previous[j] = Math.min(above + 1, left + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1));
      diagonal = above;
    }
  }
  return previous[b.length] ?? Math.max(a.length, b.length);
}

/** 1 for the same text, 0 for nothing in common. */
export function similarity(a: string, b: string): number {
  const longest = Math.max(a.length, b.length);
  return longest === 0 ? 1 : 1 - levenshtein(a, b) / longest;
}

const CLOSE_ENOUGH = 0.8;

function byName(apps: readonly InstalledApp[], wanted: string): InstalledApp[] {
  return apps.filter((app) => normaliseAppName(app.name) === wanted);
}

export function matchInstalledApp(spoken: string, apps: readonly InstalledApp[]): MatchResult {
  const wanted = normaliseAppName(spoken);
  if (wanted.length === 0) {
    return { kind: "none", suggestions: [] };
  }

  const aliasTarget = APP_ALIASES[wanted];
  if (aliasTarget !== undefined) {
    const viaAlias = byName(apps, aliasTarget);
    if (viaAlias.length === 1 && viaAlias[0] !== undefined) {
      return { kind: "match", app: viaAlias[0], how: "alias" };
    }
  }

  const exact = byName(apps, wanted);
  if (exact.length === 1 && exact[0] !== undefined) {
    return { kind: "match", app: exact[0], how: "exact" };
  }
  if (exact.length > 1) {
    return { kind: "ambiguous", candidates: exact.slice(0, 3) };
  }

  const prefix = apps.filter((app) => normaliseAppName(app.name).startsWith(`${wanted} `));
  if (prefix.length === 1 && prefix[0] !== undefined) {
    return { kind: "match", app: prefix[0], how: "prefix" };
  }
  if (prefix.length > 1) {
    return { kind: "ambiguous", candidates: prefix.slice(0, 3) };
  }

  const contains = apps.filter((app) => ` ${normaliseAppName(app.name)} `.includes(` ${wanted} `));
  if (contains.length === 1 && contains[0] !== undefined) {
    return { kind: "match", app: contains[0], how: "contains" };
  }
  if (contains.length > 1) {
    return { kind: "ambiguous", candidates: contains.slice(0, 3) };
  }

  const scored = apps
    .map((app) => ({ app, score: similarity(normaliseAppName(app.name), wanted) }))
    .sort((a, b) => b.score - a.score);
  const best = scored[0];
  const second = scored[1];
  if (best !== undefined && best.score >= CLOSE_ENOUGH && (second === undefined || best.score - second.score >= 0.1)) {
    return { kind: "match", app: best.app, how: "close" };
  }
  return { kind: "none", suggestions: scored.slice(0, 3).map((entry) => entry.app) };
}
