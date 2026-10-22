/** Static help text. Error codes are the ones in docs/api.md; each fix says what the user can do. */

export interface TroubleshootingRow {
  code: string;
  meaning: string;
  fix: string;
}

export const TASK_ERROR_ROWS: TroubleshootingRow[] = [
  { code: "device_offline", meaning: "The desktop agent is not connected.", fix: "Check that the agent is running and online on the Devices page, then retry." },
  { code: "device_not_found", meaning: "The device no longer exists.", fix: "Pair the agent again, or choose another device." },
  { code: "needs_device_selection", meaning: "The tool needs a device.", fix: "Choose a device when you create the task or schedule." },
  { code: "device_revoked", meaning: "The device was revoked.", fix: "Pair the agent again on the Devices page." },
  { code: "permission_denied", meaning: "The device permission for this tool is set to deny.", fix: "Change the permission on the Devices page if the tool should run." },
  { code: "capability_missing", meaning: "The agent does not support this tool.", fix: "Update the desktop agent." },
  { code: "resource_not_approved", meaning: "The folder, app or file is not on the agent's approved list.", fix: "Add it to the approved list on the agent, then retry." },
  { code: "invalid_arguments", meaning: "A value is missing or in the wrong form.", fix: "Edit the task or schedule arguments and try again." },
  { code: "approval_expired", meaning: "Approval was not given in time.", fix: "Create the task again and approve it while it waits." },
  { code: "approval_denied", meaning: "The task was denied.", fix: "Create the task again if you still want it to run." },
  { code: "cancelled_by_user", meaning: "The task was cancelled.", fix: "Retry it from Tasks if you still need it." },
  { code: "timeout", meaning: "The tool did not finish in time.", fix: "Retry it, and check that the device is responsive." },
  { code: "not_installed", meaning: "The app is not installed on the device.", fix: "Install the app, or choose another one." },
  { code: "execution_failed", meaning: "The tool failed on the device.", fix: "Open the task and read its events for the detail." },
  { code: "unsupported_platform", meaning: "The tool does not run on this device's operating system.", fix: "Use a device that runs a supported system for this tool." },
  { code: "agent_error", meaning: "The desktop agent reported an error.", fix: "Restart or update the desktop agent, then retry." },
];

export const API_ERROR_ROWS: TroubleshootingRow[] = [
  { code: "invalid_credentials", meaning: "The email or password is wrong.", fix: "Check both and sign in again." },
  { code: "rate_limited", meaning: "Too many requests were sent.", fix: "Wait a minute and try again." },
  { code: "validation_error", meaning: "A value was rejected.", fix: "The form marks the field. Correct it and submit again." },
  { code: "not_configured", meaning: "The AI provider has no key yet.", fix: "Add a key on the AI Models page, then test the connection." },
  { code: "memory_disabled", meaning: "Memory is off, so nothing was saved.", fix: "Turn memory on in Settings, then add the item again." },
  { code: "sensitive_content", meaning: "The text looks like a password or key, so it was not stored.", fix: "Remove the secret from the text and save again." },
  { code: "network_error", meaning: "This page could not reach the JARVIS server.", fix: "Check that the backend is running, then reload the page." },
];

export const DOCS_PATHS: Array<{ path: string; for: string }> = [
  { path: "docs/architecture.md", for: "How the modules fit together and the policy rules." },
  { path: "docs/api.md", for: "The REST and event contract, with every response shape." },
  { path: "docs/desktop-protocol.md", for: "The protocol between the backend and the Windows agent." },
  { path: "docs/integrations/", for: "Planned integrations and what each one needs." },
];

/** Keyboard shortcuts that exist in the app. Each one is a real binding; nothing here is aspirational. */
export const KEYBOARD_SHORTCUTS: Array<{ keys: string; does: string }> = [
  { keys: "Ctrl+K (Cmd+K on a Mac)", does: "Open the command palette to go to a page or start an action." },
  { keys: "Up and Down arrows", does: "Move through the palette results. Enter opens the highlighted one." },
  { keys: "Enter", does: "Send the message in the chat box." },
  { keys: "Shift+Enter", does: "Start a new line in the chat box." },
  { keys: "Escape", does: "Close a dialog or the palette." },
];

/** A link that opens the chat with a prompt in the box. The link never sends it. */
export const PROMPT_LINK_EXAMPLE = "https://jarvis.example.com/chat?prompt=Summarise%20my%20unread%20notifications";

export const PROMPT_LINK_LIMIT = 2000;

/** Steps for an iPhone Shortcut that opens the chat with the text the user typed. */
export const IOS_SHORTCUT_STEPS: string[] = [
  "Add the action Ask for Input. Set the prompt to ask for the question.",
  "Add the action URL Encode and give it the Ask for Input result.",
  "Add the action Text. Type your JARVIS address and /chat?prompt= then insert the encoded result after the equals sign.",
  "Add the action Open URLs and give it the Text result.",
  "Save the shortcut to the Home Screen. Safari opens JARVIS with the prompt in the box, and you press Enter to send it.",
];
