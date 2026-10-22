/**
 * Zod schemas for every IPC payload the main process accepts from the renderer.
 * Kept free of Electron imports so that they can be unit tested.
 */
import { z } from "zod";

const PAIRING_CODE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 -]{2,30}$/;

export const PairInputSchema = z
  .object({
    serverUrl: z.string().trim().min(8).max(2048),
    code: z.string().trim().regex(PAIRING_CODE_PATTERN),
    deviceName: z
      .string()
      .trim()
      .min(1)
      .max(120)
      .regex(/^[^\u0000-\u001f\u007f]+$/)
      .optional(),
  })
  .strict();

export type ValidatedPairInput = z.infer<typeof PairInputSchema>;

/** Payload-less channels: the renderer must send nothing. */
export const NoPayloadSchema = z.undefined();
