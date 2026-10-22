import {
  ActionError,
  cancelled,
  succeeded,
  withSimulatedPrefix,
  type ActionInput,
  type ActionOutcome,
} from "./types.js";

export async function openProject({ target, platform, signal, markStarted }: ActionInput<"open_project">): Promise<ActionOutcome> {
  const { project, app } = target;
  if (!(await platform.folderExists(project.path))) {
    throw new ActionError("execution_failed", `${project.label} folder is not available on this device.`);
  }
  const executable = await platform.findExecutable({ id: app.id, candidates: app.windows.candidates });
  if (executable === null) {
    throw new ActionError("not_installed", `${app.label} was not found in its known install locations.`);
  }
  if (signal.aborted) {
    return cancelled();
  }
  const { pid } = await platform.spawnDetached({
    executable,
    args: [project.path],
    signal,
    onStarted: () => markStarted(),
  });
  return succeeded({
    project_id: project.id,
    app_id: app.id,
    pid,
    summary: withSimulatedPrefix(platform.simulated, `Opened ${project.label} in ${app.label} (pid ${pid}).`),
  });
}
