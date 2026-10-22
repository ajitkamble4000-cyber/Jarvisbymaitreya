/**
 * POST /api/v1/agent/pair. The one-time code is the credential for this call and is never logged or echoed.
 * Every failure maps to a short message that is safe to show in the UI.
 */
import {
  PairResponseSchema,
  PROTOCOL_VERSION,
  type ActionName,
  type PairRequest,
  type PairResponse,
  type Resources,
} from "../shared/protocol.js";
import { pairingUrl, ServerUrlError } from "./server-url.js";

const PAIR_TIMEOUT_MS = 15_000;

export class PairingError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "PairingError";
    this.status = status;
  }
}

export interface PairingRequestInput {
  serverUrl: string;
  code: string;
  deviceName: string;
  deviceType: string;
  /** "windows" for the real platform, "simulated" otherwise, so simulated devices are labelled as such. */
  os: string;
  osVersion: string | null;
  agentVersion: string;
  capabilities: ActionName[];
  resources: Resources;
  fetchImpl?: typeof fetch;
}

export async function pairWithServer(input: PairingRequestInput): Promise<PairResponse> {
  let url: string;
  try {
    url = pairingUrl(input.serverUrl);
  } catch (error) {
    if (error instanceof ServerUrlError) {
      throw new PairingError(error.message);
    }
    throw error;
  }
  const body: PairRequest = {
    code: input.code,
    device_name: input.deviceName,
    device_type: input.deviceType,
    os: input.os,
    os_version: input.osVersion,
    agent_version: input.agentVersion,
    capabilities: input.capabilities,
    resources: input.resources,
  };

  const fetchImpl = input.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(PAIR_TIMEOUT_MS),
      redirect: "error",
    });
  } catch {
    throw new PairingError("Could not reach the JARVIS server. Check the address and your connection.");
  }

  if (!response.ok) {
    throw new PairingError(messageForStatus(response.status), response.status);
  }

  let json: unknown;
  try {
    json = await response.json();
  } catch {
    throw new PairingError("The server response was not recognised.", response.status);
  }
  const parsed = PairResponseSchema.safeParse(json);
  if (!parsed.success) {
    throw new PairingError("The server response was not recognised.", response.status);
  }
  if (parsed.data.protocol_version !== PROTOCOL_VERSION) {
    throw new PairingError("The server uses a protocol version this agent does not support.", response.status);
  }
  return parsed.data;
}

function messageForStatus(status: number): string {
  switch (status) {
    case 401:
      return "The pairing code is invalid or has expired. Create a new code in the web app.";
    case 422:
      return "The server rejected the pairing details. Check the code and try again.";
    case 429:
      return "Too many pairing attempts. Wait a minute and try again.";
    default:
      return `The server returned an error (HTTP ${status}).`;
  }
}
