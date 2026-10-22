import { ActionError, cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

export async function launchApp({ target, platform, signal, markStarted }: ActionInput<"launch_app">): Promise<ActionOutcome> {
  const { app } = target;
  const executable = await platform.findExecutable({ id: app.id, candidates: app.windows.candidates });
  if (executable === null) {
    throw new ActionError("not_installed", `${app.label} was not found in its known install locations.`);
  }
  if (signal.aborted) {
    return cancelled();
  }
  const { pid } = await platform.spawnDetached({
    executable,
    args: [],
    signal,
    onStarted: () => markStarted(),
  });
  return succeeded({
    pid,
    app_id: app.id,
    verified: false,
    summary: withSimulatedPrefix(platform.simulated, `Started ${app.label} (pid ${pid}).`),
  });
}
