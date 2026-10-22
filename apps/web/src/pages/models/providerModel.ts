import type { ProviderCapabilities, ProviderOut, ProviderStatus, ProviderUsage } from "../../types/api";
import type { StatusTone } from "../../components/ui/StatusDot";

export const CAPABILITY_LABELS: Record<keyof ProviderCapabilities, string> = {
  streaming: "Streaming replies",
  tool_calling: "Tool calling",
  multimodal_input: "Image and other media input",
  usage_reporting: "Usage reporting",
  model_listing: "Model listing",
};

export const STATUS_PRESENTATION: Record<ProviderStatus, { tone: StatusTone; label: string }> = {
  not_configured: { tone: "muted", label: "Not configured" },
  untested: { tone: "warning", label: "Not tested yet" },
  connected: { tone: "success", label: "Connected" },
  error: { tone: "danger", label: "Error" },
};

export const TIMEOUT_RANGE = { min: 1, max: 120 } as const;
export const RETRY_RANGE = { min: 0, max: 5 } as const;

export interface TuningInput {
  timeout: string;
  retries: string;
}

export interface TuningResult {
  timeout_seconds?: number;
  max_retries?: number;
  errors: { timeout?: string; retries?: string };
}

/** Validates timeout (1..120 seconds) and retries (0..5). Pure. */
export function parseTuning(input: TuningInput): TuningResult {
  const errors: TuningResult["errors"] = {};
  const timeout = Number(input.timeout);
  const retries = Number(input.retries);
  const result: TuningResult = { errors };
  if (!input.timeout.trim() || !Number.isFinite(timeout) || timeout < TIMEOUT_RANGE.min || timeout > TIMEOUT_RANGE.max) {
    errors.timeout = `Enter a timeout from ${TIMEOUT_RANGE.min} to ${TIMEOUT_RANGE.max} seconds.`;
  } else {
    result.timeout_seconds = timeout;
  }
  if (!input.retries.trim() || !Number.isInteger(retries) || retries < RETRY_RANGE.min || retries > RETRY_RANGE.max) {
    errors.retries = `Enter a whole number of retries from ${RETRY_RANGE.min} to ${RETRY_RANGE.max}.`;
  } else {
    result.max_retries = retries;
  }
  return result;
}

/** Tokens are "estimated" when the usage report says so, otherwise "measured". */
export function tokenSourceLabel(usage: ProviderUsage): "measured" | "estimated" {
  return usage.totals.tokens_estimated ? "estimated" : "measured";
}

/** Cost is shown only when the source reports a number. Otherwise it is "unavailable", never zero. */
export function costLabel(usage: ProviderUsage): string {
  if (usage.estimated_cost_usd === null || usage.cost_source === "unavailable") return "Unavailable";
  return `$${usage.estimated_cost_usd.toFixed(2)} (${usage.cost_source})`;
}

export function credentialHintText(provider: ProviderOut): string {
  if (!provider.configured && !provider.credential_hint) return "No key is stored for this provider.";
  if (provider.credential_hint) return `A key is stored and ends in ${provider.credential_hint}. Enter a new key to replace it. The stored key is never shown.`;
  return "A key is stored. The stored key is never shown.";
}

export function capabilityList(capabilities: ProviderCapabilities): string[] {
  return (Object.keys(CAPABILITY_LABELS) as Array<keyof ProviderCapabilities>)
    .filter((key) => capabilities[key])
    .map((key) => CAPABILITY_LABELS[key]);
}
