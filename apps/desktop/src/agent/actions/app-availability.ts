import { cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

export async function appAvailability({ target, platform, signal }: ActionInput<"app_availability">): Promise<ActionOutcome> {
  const { app } = target;
  if (signal.aborted) {
    return cancelled();
  }
  const executable = await platform.findExecutable({ id: app.id, candidates: app.windows.candidates });
  const installed = executable !== null;
  return succeeded({
    app_id: app.id,
    installed,
    summary: withSimulatedPrefix(
      platform.simulated,
      installed
        ? `${app.label} is installed.`
        : `${app.label} was not found in its known install locations.`,
    ),
  });
}
