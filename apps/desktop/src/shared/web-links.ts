/**
 * Links the agent builds itself for compose_email and web_search. The host and path are fixed here; the backend
 * supplies only the text that goes into the query string, so a command can never point the browser elsewhere.
 */

/** Longest link handed to the browser. Gmail refuses much longer compose links. */
export const MAX_LINK_LENGTH = 8000;

export interface EmailDraft {
  to: readonly string[];
  cc?: readonly string[] | undefined;
  subject: string;
  body: string;
}

function encode(value: string): string {
  // encodeURIComponent keeps spaces as %20 and new lines as %0A, which Gmail shows as written.
  return encodeURIComponent(value);
}

/**
 * Gmail's compose window, filled in. Nothing is sent: the person reviews the draft in Gmail and presses Send.
 * The link is not documented by Google; tf=cm opens the compose window and view=cm&fs=1 keeps older Gmail working.
 */
export function gmailComposeUrl(draft: EmailDraft): string {
  const parts = [
    "view=cm",
    "fs=1",
    "tf=cm",
    `to=${encode(draft.to.join(","))}`,
    ...(draft.cc !== undefined && draft.cc.length > 0 ? [`cc=${encode(draft.cc.join(","))}`] : []),
    `su=${encode(draft.subject)}`,
    `body=${encode(draft.body)}`,
  ];
  return `https://mail.google.com/mail/?${parts.join("&")}`;
}

export const SEARCH_SITES = ["google", "youtube", "spotify"] as const;
export type SearchSite = (typeof SEARCH_SITES)[number];

const SITE_LABELS: Record<SearchSite, string> = {
  google: "Google",
  youtube: "YouTube",
  spotify: "Spotify",
};

export function searchSiteLabel(site: SearchSite): string {
  return SITE_LABELS[site];
}

export function searchUrl(site: SearchSite, query: string): string {
  const q = encode(query.trim());
  switch (site) {
    case "google":
      return `https://www.google.com/search?q=${q}`;
    case "youtube":
      return `https://www.youtube.com/results?search_query=${q}`;
    case "spotify":
      return `https://open.spotify.com/search/${q}`;
  }
}
