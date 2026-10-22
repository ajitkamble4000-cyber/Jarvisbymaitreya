/**
 * HMAC-SHA256 signing of commands and cancels. Must stay identical to services/api/app/security/signing.py:
 * the signed string is the six fields joined with a single LF, the key is the UTF-8 bytes of the device secret,
 * and the signature is lowercase hex.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

export const SIGNED_FIELDS = ["id", "action", "issued_at", "expires_at", "nonce", "args_json"] as const;

export type SignedFields = Record<(typeof SIGNED_FIELDS)[number], string>;

export function signingString(fields: SignedFields): string {
  return SIGNED_FIELDS.map((name) => fields[name]).join("\n");
}

export function signFields(secret: string, fields: SignedFields): string {
  return createHmac("sha256", Buffer.from(secret, "utf8"))
    .update(signingString(fields), "utf8")
    .digest("hex");
}

/** Constant-time comparison. Unequal lengths are rejected before timingSafeEqual, which requires equal lengths. */
export function verifyFields(secret: string, fields: SignedFields, signature: string): boolean {
  const expected = Buffer.from(signFields(secret, fields), "utf8");
  const received = Buffer.from(signature, "utf8");
  if (received.length !== expected.length) {
    return false;
  }
  return timingSafeEqual(expected, received);
}
