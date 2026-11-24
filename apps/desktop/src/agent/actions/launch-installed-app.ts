import { matchInstalledApp } from "../../shared/app-matcher.js";
import { ActionError, cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

/**
 * Opens any app from the Start menu by the name the person said ("open Wispr Flow"). The name is matched on this
 * device against its own Start-menu list; only a clear match is started. Otherwise the closest names are returned
 * so the assistant can ask which one was meant.
 */
export async function launchInstalledApp({
  target,
  platform,
  signal,
  markStarted,
}: ActionInput<"launch_installed_app">): Promise<ActionOutcome> {
  const apps = await platform.listStartApps(signal);
  if (signal.aborted) {
    return cancelled();
  }
  const match = matchInstalledApp(target.name, apps);
  if (match.kind === "ambiguous") {
    return {
      status: "failed",
      error: {
        code: "invalid_arguments",
        message: `More than one app matches "${target.name}". Say which one.`,
        details: { candidates: match.candidates.map((app) => app.name) },
      },
    };
  }
  if (match.kind === "none") {
    const error = new ActionError("not_installed", `No installed app matches "${target.name}".`);
    return {
      status: "failed",
      error: { code: error.code, message: error.message, details: { suggestions: match.suggestions.map((app) => app.name) } },
    };
  }
  await platform.launchStartApp(match.app.appId, signal, () => markStarted());
  return succeeded({
    app: match.app.name,
    matched_by: match.how,
    verified: false,
    summary: withSimulatedPrefix(platform.simulated, `Opened ${match.app.name}.`),
  });
}
