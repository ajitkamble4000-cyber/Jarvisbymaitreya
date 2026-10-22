import {
  ActionError,
  cancelled,
  succeeded,
  withSimulatedPrefix,
  type ActionInput,
  type ActionOutcome,
} from "./types.js";

export async function openFolder({ target, platform, signal, markStarted }: ActionInput<"open_folder">): Promise<ActionOutcome> {
  const { folder } = target;
  if (!(await platform.folderExists(folder.path))) {
    throw new ActionError("execution_failed", `${folder.label} is not available on this device.`);
  }
  if (signal.aborted) {
    return cancelled();
  }
  markStarted();
  await platform.openFolder(folder.path);
  return succeeded({
    folder_id: folder.id,
    verified: false,
    summary: withSimulatedPrefix(platform.simulated, `Asked File Explorer to open ${folder.label}.`),
  });
}
