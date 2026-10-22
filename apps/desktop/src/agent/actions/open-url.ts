import { cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

export async function openUrl({ target, platform, signal, markStarted }: ActionInput<"open_url">): Promise<ActionOutcome> {
  const { url } = target;
  if (signal.aborted) {
    return cancelled();
  }
  // Once the request is handed to the operating system it cannot be recalled, so cancel is refused from here on.
  markStarted();
  await platform.openExternal(url.url);
  return succeeded({
    url: url.url,
    verified: false,
    summary: withSimulatedPrefix(platform.simulated, `Asked the default browser to open ${url.label}.`),
  });
}
