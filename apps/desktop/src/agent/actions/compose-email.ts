import { MAX_LINK_LENGTH, gmailComposeUrl } from "../../shared/web-links.js";
import { ActionError, cancelled, succeeded, withSimulatedPrefix, type ActionInput, type ActionOutcome } from "./types.js";

/**
 * Opens a filled-in Gmail draft in the default browser, in whichever Google account is signed in there.
 * Nothing is sent by the agent: the person reads the draft in Gmail and presses Send themselves.
 */
export async function composeEmail({ target, platform, signal, markStarted }: ActionInput<"compose_email">): Promise<ActionOutcome> {
  const { email } = target;
  const url = gmailComposeUrl(email);
  if (url.length > MAX_LINK_LENGTH) {
    throw new ActionError("invalid_arguments", "The email is too long to open as a Gmail draft. Shorten the body.");
  }
  if (signal.aborted) {
    return cancelled();
  }
  markStarted();
  await platform.openExternal(url);
  const recipients = email.to.join(", ");
  return succeeded({
    to: email.to,
    subject: email.subject,
    sent: false,
    verified: false,
    summary: withSimulatedPrefix(
      platform.simulated,
      `Opened a Gmail draft to ${recipients}. Nothing is sent until you press Send in Gmail.`,
    ),
  });
}
