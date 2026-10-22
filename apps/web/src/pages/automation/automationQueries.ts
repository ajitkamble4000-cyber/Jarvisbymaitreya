/** Emergency stop endpoints (docs/api.md "Tasks: emergency stop"). */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../../components/ui/Toast";
import { api } from "../../services/api";
import { qk } from "../../services/queries";

export const EMERGENCY_STOP_PHRASE = "STOP";

export interface EmergencyStopResult {
  stopped: boolean;
  /** Queued and awaiting-approval tasks that were cancelled now. */
  cancelled: number;
  /** Running tasks that were asked to stop. They stay running until the device confirms. */
  cancel_requested: number;
}

/** The toast text. Running tasks are reported as asked to stop, never as cancelled. */
export function emergencyStopMessage(result: Pick<EmergencyStopResult, "cancelled" | "cancel_requested">): string {
  const cancelled = `${result.cancelled} ${result.cancelled === 1 ? "task" : "tasks"} cancelled.`;
  if (result.cancel_requested === 0) return cancelled;
  const asked = `${result.cancel_requested} running ${result.cancel_requested === 1 ? "task" : "tasks"} asked to stop on the device.`;
  return `${cancelled} ${asked}`;
}

function refreshAutomation(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([queryClient.invalidateQueries({ queryKey: qk.settings }), queryClient.invalidateQueries({ queryKey: qk.tasks })]);
}

/**
 * Stops automation. The typed STOP is sent so that the server checks the same confirmation. The
 * success message lives in the hook because the caller may unmount when settings refresh.
 */
export function useEmergencyStop() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  return useMutation({
    mutationFn: () => api.post<EmergencyStopResult>("/tasks/emergency-stop", { confirm: EMERGENCY_STOP_PHRASE }),
    onSuccess: (result) => {
      toast({ tone: "success", title: "Automation stopped", description: emergencyStopMessage(result) });
    },
    onSettled: () => refreshAutomation(queryClient),
  });
}

export function useEmergencyResume() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  return useMutation({
    mutationFn: () => api.post<{ stopped: boolean }>("/tasks/emergency-resume"),
    onSuccess: () => {
      toast({ tone: "success", title: "Automation resumed" });
    },
    onSettled: () => refreshAutomation(queryClient),
  });
}
