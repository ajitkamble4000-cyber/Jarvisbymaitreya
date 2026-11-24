import type { ActionHandlers } from "./types.js";
import { appAvailability } from "./app-availability.js";
import { composeEmail } from "./compose-email.js";
import { createFiles } from "./create-files.js";
import { launchInstalledApp } from "./launch-installed-app.js";
import { webSearch } from "./web-search.js";
import { launchApp } from "./launch-app.js";
import { openFolder } from "./open-folder.js";
import { openProject } from "./open-project.js";
import { openUrl } from "./open-url.js";
import { readFile } from "./read-file.js";
import { runCheck } from "./run-check.js";
import { systemInfo } from "./system-info.js";

export type { ActionHandlers, ActionInput, ActionOutcome } from "./types.js";

/** One handler per supported action. The dispatcher looks handlers up by action name only. */
export const DEFAULT_HANDLERS: ActionHandlers = {
  launch_app: launchApp,
  open_url: openUrl,
  open_folder: openFolder,
  open_project: openProject,
  system_info: systemInfo,
  read_file: readFile,
  app_availability: appAvailability,
  run_check: runCheck,
  compose_email: composeEmail,
  web_search: webSearch,
  launch_installed_app: launchInstalledApp,
  create_files: createFiles,
};
