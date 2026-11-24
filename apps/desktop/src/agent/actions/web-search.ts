import { searchSiteLabel, searchUrl } from "../../shared/web-links.js";
import { cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

/** Opens a search on Google, YouTube or Spotify in the default browser. The site's address is fixed by the agent. */
export async function webSearch({ target, platform, signal, markStarted }: ActionInput<"web_search">): Promise<ActionOutcome> {
  const url = searchUrl(target.site, target.query);
  if (signal.aborted) {
    return cancelled();
  }
  markStarted();
  await platform.openExternal(url);
  const label = searchSiteLabel(target.site);
  return succeeded({
    site: target.site,
    query: target.query,
    verified: false,
    summary: withSimulatedPrefix(platform.simulated, `Searched ${label} for "${target.query}".`),
  });
}
