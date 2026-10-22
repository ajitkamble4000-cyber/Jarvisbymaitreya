/**
 * Schedule endpoints used by the Schedule page. Keys sit under qk.schedules so that the
 * schedule.updated live event refreshes the list and the run history together.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, buildQuery } from "../../services/api";
import { qk } from "../../services/queries";
import type { ScheduleOut } from "../../types/api";
import type { ScheduleCreateBody } from "./scheduleModel";

export interface SchedulePreviewResult {
  /** UTC instants, ISO 8601, at most five, in order. Empty when no run is left. */
  runs: string[];
}

/**
 * The next runs of a timing spec. The POST sends nothing to store. The key is the spec's JSON, so the same spec
 * is not requested twice, and the caller debounces the spec before it reaches here.
 */
export function useSchedulePreview(spec: ScheduleCreateBody["schedule"] | null) {
  const key = spec ? JSON.stringify(spec) : null;
  return useQuery({
    queryKey: ["schedules", "preview", key],
    queryFn: () => api.post<SchedulePreviewResult>("/schedules/preview", spec),
    enabled: spec !== null,
    staleTime: 60_000,
  });
}

export interface ScheduleRunOut {
  id: string;
  scheduled_for: string;
  status: string;
  attempt: number;
  task_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: Record<string, unknown> | null;
}

export interface SchedulePatch {
  name?: string;
  enabled?: boolean;
  paused?: boolean;
}

export const scheduleRunsKey = (id: string) => [...qk.schedules, "runs", id] as const;

export function useCreateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ScheduleCreateBody) => api.post<ScheduleOut>("/schedules", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.schedules }),
  });
}

export function useUpdateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: SchedulePatch }) =>
      api.patch<ScheduleOut>(`/schedules/${encodeURIComponent(id)}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.schedules }),
  });
}

export function useDeleteSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/schedules/${encodeURIComponent(id)}`),
    onSuccess: (_result, id) => {
      queryClient.removeQueries({ queryKey: scheduleRunsKey(id) });
      return queryClient.invalidateQueries({ queryKey: qk.schedules });
    },
  });
}

export function useScheduleRuns(id: string, enabled: boolean) {
  return useQuery({
    queryKey: scheduleRunsKey(id),
    queryFn: async () =>
      (await api.get<{ items: ScheduleRunOut[] }>(`/schedules/${encodeURIComponent(id)}/runs${buildQuery({ limit: 20 })}`)).items,
    enabled,
  });
}
