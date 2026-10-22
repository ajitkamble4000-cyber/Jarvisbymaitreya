import { describe, expect, it } from "vitest";
import { signFields, signingString, verifyFields, type SignedFields } from "../src/shared/canonical.js";

/*
 * Parity vectors. Computed with the backend's own code:
 *   services/api/.venv/bin/python -c "from app.security.signing import sign_fields; ..."
 * The backend signs the six fields joined with LF, keyed by the UTF-8 bytes of the device secret.
 */
const VECTOR_FIELDS: SignedFields = {
  id: "9b7e5c1a-2f3d-4e8a-9b6c-1d2e3f4a5b6c",
  action: "launch_app",
  issued_at: "2026-10-19T04:51:37Z",
  expires_at: "2026-10-19T04:52:37Z",
  nonce: "b64url-test-nonce-0123456789",
  args_json: '{"app_id":"chrome"}',
};
const VECTOR_SECRET = "test-device-secret-ÄÖ-ü";
const VECTOR_HEX = "e2e8375c0fe07bccb83815e26f8ff78954967ce61b1c6371770a70b541b52599";

const SECOND_FIELDS: SignedFields = { ...VECTOR_FIELDS, args_json: '{"app_id":"chrome","x":1}' };
const SECOND_SECRET = "abc";
const SECOND_HEX = "0592d0618bdebe1b35b9c619b7198cd0cc20d23764066c837f81786604462809";

describe("canonical signing parity with the backend", () => {
  it("produces the same hex as app.security.signing.sign_fields for a non-ASCII secret", () => {
    expect(signFields(VECTOR_SECRET, VECTOR_FIELDS)).toBe(VECTOR_HEX);
  });

  it("produces the same hex for a second vector with different arguments", () => {
    expect(signFields(SECOND_SECRET, SECOND_FIELDS)).toBe(SECOND_HEX);
  });

  it("joins the six fields with a single LF in the protocol order", () => {
    expect(signingString(VECTOR_FIELDS)).toBe(
      [
        "9b7e5c1a-2f3d-4e8a-9b6c-1d2e3f4a5b6c",
        "launch_app",
        "2026-10-19T04:51:37Z",
        "2026-10-19T04:52:37Z",
        "b64url-test-nonce-0123456789",
        '{"app_id":"chrome"}',
      ].join("\n"),
    );
  });
});

describe("verifyFields", () => {
  it("accepts the backend signature", () => {
    expect(verifyFields(VECTOR_SECRET, VECTOR_FIELDS, VECTOR_HEX)).toBe(true);
  });

  it("rejects a signature made with another secret", () => {
    expect(verifyFields("another-secret", VECTOR_FIELDS, VECTOR_HEX)).toBe(false);
  });

  it("rejects a tampered argument string", () => {
    const tampered = { ...VECTOR_FIELDS, args_json: '{"app_id":"vscode"}' };
    expect(verifyFields(VECTOR_SECRET, tampered, VECTOR_HEX)).toBe(false);
  });

  it("rejects a signature of the wrong length without comparing bytes", () => {
    expect(verifyFields(VECTOR_SECRET, VECTOR_FIELDS, VECTOR_HEX.slice(0, 10))).toBe(false);
    expect(verifyFields(VECTOR_SECRET, VECTOR_FIELDS, `${VECTOR_HEX}00`)).toBe(false);
  });

  it("rejects an upper-case hex signature, as the backend does", () => {
    expect(verifyFields(VECTOR_SECRET, VECTOR_FIELDS, VECTOR_HEX.toUpperCase())).toBe(false);
  });

  it("rejects an empty signature", () => {
    expect(verifyFields(VECTOR_SECRET, VECTOR_FIELDS, "")).toBe(false);
  });
});
