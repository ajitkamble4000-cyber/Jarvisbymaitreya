/** Developer console endpoints (docs/api.md "Audit, analytics, developer"). Responses are already redacted by the API. */

import { useQuery } from "@tanstack/react-query";
import { api, buildQuery } from "../../services/api";
import type { TaskDetailResponse } from "../../types/api";

export const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

export interface VersionInfo {
  app_version: string;
  python_version: string;
  environment: string;
  protocol_version: number;
  background_workers: boolean;
}

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  message: string;
  request_id: string | null;
  task_id: string | null;
  device_id: string | null;
  provider_id: string | null;
}

export interface ConfigCheck {
  key: string;
  status: "ok" | "warning" | "error";
  message: string;
}

export const LOG_LIMIT = 200;
export const LOG_REFRESH_MS = 5000;

export function useVersionQuery() {
  return useQuery({
    queryKey: ["developer", "version"] as const,
    queryFn: () => api.get<VersionInfo>("/developer/version"),
  });
}

export function useLogsQuery(level: LogLevel, autoRefresh: boolean) {
  return useQuery({
    queryKey: ["developer", "logs", level] as const,
    queryFn: async () => (await api.get<{ items: LogEntry[] }>(`/developer/logs${buildQuery({ level, limit: LOG_LIMIT })}`)).items,
    refetchInterval: autoRefresh ? LOG_REFRESH_MS : false,
  });
}

export function useConfigCheckQuery() {
  return useQuery({
    queryKey: ["developer", "config-check"] as const,
    queryFn: async () => (await api.get<{ checks: ConfigCheck[] }>("/developer/config-check")).checks,
  });
}

export function useTraceQuery(taskId: string | null) {
  return useQuery({
    queryKey: ["developer", "trace", taskId ?? ""] as const,
    queryFn: () => api.get<TaskDetailResponse>(`/developer/traces/${encodeURIComponent(taskId ?? "")}`),
    enabled: taskId !== null,
    retry: false,
  });
}
