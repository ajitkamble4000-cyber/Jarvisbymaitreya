import type { DeviceOut, PermissionMode, ToolOut } from "../../types/api";
import type { StatusTone } from "../../components/ui/StatusDot";

export const PERMISSION_MODES: readonly PermissionMode[] = ["ask", "deny", "allow"];

export const PERMISSION_LABEL: Record<PermissionMode, string> = {
  ask: "Ask each time",
  deny: "Deny",
  allow: "Allow without asking",
};

/**
 * "Allow" is offered only for capabilities whose tool is LOW risk, which is the API rule. A
 * capability with no known tool is not offered "allow". Pure.
 */
export function allowModeOffered(capability: string, tools: readonly ToolOut[] | undefined): boolean {
  const tool = tools?.find((item) => item.name === capability);
  return tool?.risk_level === "low";
}

export function permissionOptions(capability: string, tools: readonly ToolOut[] | undefined): PermissionMode[] {
  return PERMISSION_MODES.filter((mode) => mode !== "allow" || allowModeOffered(capability, tools));
}

export function deviceStatus(device: DeviceOut): { tone: StatusTone; label: string } {
  if (device.connection_status === "revoked" || device.pairing_status === "revoked") return { tone: "danger", label: "Revoked" };
  if (device.connection_status === "online") return { tone: "success", label: "Online" };
  return { tone: "muted", label: "Offline" };
}

/** Labels only. Resource paths never leave the agent, so none are shown. Checks are listed by {@link checkLabels}. */
export function resourceLabels(device: DeviceOut): string[] {
  const { apps, urls, folders, projects, files } = device.resources;
  return [...apps, ...urls, ...folders, ...projects, ...files].map((resource) => resource.label);
}

/** Labels of the approved checks. The command behind each check stays on the device. */
export function checkLabels(device: DeviceOut): string[] {
  return (device.resources.checks ?? []).map((check) => check.label);
}

/** Whole seconds until a pairing code expires, never negative. Pure. */
export function secondsRemaining(expiresAt: string, now: number): number {
  const end = new Date(expiresAt).getTime();
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.ceil((end - now) / 1000));
}
