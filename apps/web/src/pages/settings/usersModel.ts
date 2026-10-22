/**
 * Rules for the owner's Users section. They mirror docs/phase3/users.md, so the form explains a
 * mistake before the server does. The server still checks everything. Pure, so each rule can be tested.
 */

import type { AdminUser } from "../../types/api";

export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 256;
export const DISPLAY_NAME_MAX = 120;

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// Control characters are rejected, as the server does for display names.
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;

export function validatePassword(password: string): string | null {
  if (password.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters for the password.`;
  if (password.length > PASSWORD_MAX) return `Use ${PASSWORD_MAX} characters or fewer.`;
  return null;
}

export interface MemberDraft {
  email: string;
  displayName: string;
  password: string;
}

export interface MemberErrors {
  email?: string;
  displayName?: string;
  password?: string;
}

/** Field messages for the create form. An empty result means the draft can be sent. */
export function validateMember(draft: MemberDraft): MemberErrors {
  const errors: MemberErrors = {};
  const email = draft.email.trim().toLowerCase();
  if (!EMAIL_SHAPE.test(email) || email.length > 254) errors.email = "Enter an email address such as sam@example.com.";

  const name = draft.displayName.trim();
  if (name.length < 1 || name.length > DISPLAY_NAME_MAX || CONTROL_CHARACTERS.test(name)) {
    errors.displayName = `Enter a name of 1 to ${DISPLAY_NAME_MAX} characters.`;
  }

  const passwordProblem = validatePassword(draft.password);
  if (passwordProblem) errors.password = passwordProblem;
  return errors;
}

/** The status as plain words. Colour is never the only signal. */
export function userStatusText(user: AdminUser): string {
  if (user.role === "owner") return "Owner account";
  return user.is_active ? "Active" : "Deactivated";
}

/** The owner account cannot be deactivated or given a new password here, so it has no actions. */
export function canManageUser(user: AdminUser): boolean {
  return user.role !== "owner";
}

export function lastSignInText(user: AdminUser): string {
  if (!user.last_login_at) return "Has not signed in yet";
  const date = new Date(user.last_login_at);
  return Number.isNaN(date.getTime()) ? "Signed in before" : `Last signed in ${date.toLocaleDateString()}`;
}
