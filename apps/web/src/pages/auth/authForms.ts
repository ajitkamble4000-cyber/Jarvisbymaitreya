import { z } from "zod";
import { ApiError } from "../../services/api";

export const MIN_PASSWORD_LENGTH = 12;

/**
 * Returns the page to go back to after sign-in. Only same-site paths are honoured, so the
 * return address cannot send the viewer to another site.
 */
export function safeReturnPath(state: unknown): string {
  const from = typeof state === "object" && state !== null && "from" in state ? (state as { from?: unknown }).from : undefined;
  if (typeof from !== "string" || !from.startsWith("/") || from.startsWith("//")) return "/overview";
  if (from === "/login" || from.startsWith("/login?") || from === "/setup" || from.startsWith("/setup?")) return "/overview";
  return from;
}

export const loginSchema = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

export const setupSchema = z
  .object({
    email: z.email("Enter a valid email address."),
    display_name: z.string().trim().max(120, "Use 120 characters or fewer."),
    password: z.string().min(MIN_PASSWORD_LENGTH, `Use at least ${MIN_PASSWORD_LENGTH} characters.`),
    confirm: z.string(),
  })
  .refine((values) => values.password === values.confirm, {
    message: "The passwords do not match.",
    path: ["confirm"],
  });

export type FieldErrors = Partial<Record<string, string>>;

/** Turns zod issues into one message per field. The first issue for a field wins. */
export function fieldErrorsFromIssues(issues: ReadonlyArray<{ path: PropertyKey[]; message: string }>): FieldErrors {
  const errors: FieldErrors = {};
  for (const issue of issues) {
    const key = String(issue.path[0] ?? "form");
    if (errors[key] === undefined) errors[key] = issue.message;
  }
  return errors;
}

/** Maps server validation details (details.fields[{field, message}]) onto form fields. */
export function fieldErrorsFromApiError(error: unknown): { fields: FieldErrors; message: string | null } {
  if (!(error instanceof ApiError)) return { fields: {}, message: "Something went wrong. Try again." };
  const fields: FieldErrors = {};
  const details = error.details;
  if (typeof details === "object" && details !== null && "fields" in details && Array.isArray(details.fields)) {
    for (const entry of details.fields as unknown[]) {
      if (typeof entry === "object" && entry !== null && "field" in entry && "message" in entry) {
        const field = (entry as { field: unknown }).field;
        const message = (entry as { message: unknown }).message;
        if (typeof field === "string" && typeof message === "string") fields[field] = message;
      }
    }
  }
  return { fields, message: error.message };
}

/** Friendly copy for sign-in failures. Never reveals whether the email exists. */
export function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "invalid_credentials") return "The email or password is not correct.";
    if (error.code === "rate_limited") return "Too many sign-in attempts. Wait a minute and try again.";
    return error.message;
  }
  return "Something went wrong. Try again.";
}
