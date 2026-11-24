import { ActionError, cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

/**
 * Creates a project's folders and files inside an approved workspace, then optionally opens the project in an approved
 * editor (VS Code by default when the assistant asks for it). Existing files are never overwritten.
 */
export async function createFiles({ target, platform, signal, markStarted }: ActionInput<"create_files">): Promise<ActionOutcome> {
  if (signal.aborted) {
    return cancelled();
  }
  markStarted();
  const result = await platform.createFiles({
    root: target.workspace.path,
    folder: target.folder,
    files: target.files.map((file) => ({ path: file.path, content: file.content })),
  });
  const count = result.created.length;
  const written = `Created ${count} file${count === 1 ? "" : "s"} in ${target.folder} (${target.workspace.label})`;

  if (target.app === null) {
    return succeeded({
      workspace_id: target.workspace.id,
      folder: target.folder,
      created: result.created,
      opened_in: null,
      summary: withSimulatedPrefix(platform.simulated, `${written}.`),
    });
  }

  const app = target.app;
  const executable = await platform.findExecutable({ id: app.id, candidates: app.windows.candidates });
  if (executable === null) {
    // The files exist; only opening them failed. Say both, so nothing is reported as undone.
    throw new ActionError("not_installed", `${written}, but ${app.label} was not found, so it was not opened.`);
  }
  const { pid } = await platform.spawnDetached({
    executable,
    args: [result.folderPath],
    signal: new AbortController().signal,
    onStarted: () => undefined,
  });
  return succeeded({
    workspace_id: target.workspace.id,
    folder: target.folder,
    created: result.created,
    opened_in: app.id,
    pid,
    summary: withSimulatedPrefix(platform.simulated, `${written} and opened it in ${app.label}.`),
  });
}
