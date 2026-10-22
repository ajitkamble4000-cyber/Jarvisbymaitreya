/**
 * TanStack Query hooks, one per endpoint used by the core pages. Query keys are defined only in
 * `qk`. Live invalidation is driven by invalidationsForEvent, which useEventStream applies.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api, buildQuery } from "./api";
import type {
  AdminUser,
  AdminUserList,
  CallsToday,
  SearchResult,
  AuditResponse,
  AuthStatus,
  ConversationDetail,
  ConversationOut,
  DeviceOut,
  HealthOut,
  LiveEvent,
  MessageOut,
  Page,
  PairingCodeOut,
  PermissionMode,
  PingResult,
  ProviderModel,
  ProviderOut,
  ProviderTestResult,
  ProviderUsage,
  ScheduleOut,
  SettingsDoc,
  SettingsPatch,
  TaskDetailResponse,
  TaskListResponse,
  TaskOut,
  TaskStatus,
  ToolOut,
} from "../types/api";

export interface ConversationListParams {
  query: string;
  archived: boolean;
}

export interface TaskListParams {
  status?: TaskStatus | "all";
  q?: string;
  limit?: number;
  offset?: number;
}

export const qk = {
  authStatus: ["auth", "status"] as const,
  users: ["users"] as const,
  settings: ["settings"] as const,
  health: ["health"] as const,
  providers: ["providers"] as const,
  providerModels: (id: string) => ["providers", "models", id] as const,
  providerUsage: (days: number) => ["providers", "usage", days] as const,
  callsToday: ["analytics", "calls-today"] as const,
  conversations: ["conversations"] as const,
  conversationList: (params: ConversationListParams) => ["conversations", "list", params] as const,
  conversation: (id: string) => ["conversations", "detail", id] as const,
  tasks: ["tasks"] as const,
  taskList: (params: TaskListParams) => ["tasks", "list", params] as const,
  taskCounts: ["tasks", "counts"] as const,
  task: (id: string) => ["tasks", "detail", id] as const,
  devices: ["devices"] as const,
  tools: ["tools"] as const,
  schedules: ["schedules"] as const,
  audit: (limit: number) => ["audit", limit] as const,
  notifications: ["notifications"] as const,
};

/**
 * Maps a live event to the query keys that must be refetched. Events are signals only, so the
 * handler refetches REST data rather than trusting the payload.
 */
export function invalidationsForEvent(event: LiveEvent): QueryKey[] {
  switch (event.type) {
    case "task.updated":
      return [qk.tasks];
    case "approval.requested":
      return [qk.tasks, qk.notifications];
    case "device.updated":
      return [qk.devices];
    case "schedule.updated":
      return [qk.schedules];
    case "chat.message":
      return [qk.conversation(event.data.conversation_id), ["conversations", "list"]];
    case "notification.created":
      return [qk.notifications];
    case "heartbeat":
      return [];
    default:
      return [];
  }
}

// ---------------------------------------------------------------- health and auth

export function useAuthStatusQuery() {
  return useQuery({
    queryKey: qk.authStatus,
    queryFn: () => api.get<AuthStatus>("/auth/status"),
    staleTime: 30_000,
  });
}

export function useHealthQuery() {
  return useQuery({
    queryKey: qk.health,
    queryFn: () => api.get<HealthOut>("/health"),
    refetchInterval: 30_000,
  });
}

// ---------------------------------------------------------------- settings

/** Owner only. The server returns 403 for a member, and the Users section is not shown to members. */
export function useAdminUsersQuery(enabled = true) {
  return useQuery({
    queryKey: qk.users,
    queryFn: () => api.get<AdminUserList>("/users"),
    enabled,
  });
}

export interface CreateUserBody {
  email: string;
  display_name: string;
  password: string;
  role: "member";
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateUserBody) => api.post<AdminUser>("/users", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.users }),
  });
}

export interface UserStatusPatch {
  id: string;
  patch: { is_active?: boolean; display_name?: string };
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: UserStatusPatch) => api.patch<AdminUser>(`/users/${encodeURIComponent(id)}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.users }),
  });
}

/** The server revokes the member's sessions when the password changes, so the list needs no update. */
export function useResetUserPassword() {
  return useMutation({
    mutationFn: ({ id, newPassword }: { id: string; newPassword: string }) =>
      api.post<void>(`/users/${encodeURIComponent(id)}/password`, { new_password: newPassword }),
  });
}

export function useSettingsQuery(enabled = true) {
  return useQuery({
    queryKey: qk.settings,
    queryFn: () => api.get<SettingsDoc>("/settings"),
    enabled,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.patch<SettingsDoc>("/settings", patch),
    onSuccess: (doc) => {
      queryClient.setQueryData(qk.settings, doc);
    },
  });
}

// ---------------------------------------------------------------- AI providers

export function useProvidersQuery() {
  return useQuery({
    queryKey: qk.providers,
    queryFn: async () => (await api.get<{ items: ProviderOut[] }>("/providers")).items,
  });
}

/** Chat provider calls made today (UTC) against the daily call limit. */
export function useCallsTodayQuery() {
  return useQuery({
    queryKey: qk.callsToday,
    queryFn: () => api.get<CallsToday>("/analytics/calls-today"),
  });
}

export interface ProviderPatch {
  enabled?: boolean;
  selected_model?: string | null;
  timeout_seconds?: number;
  max_retries?: number;
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ProviderPatch }) => api.put<ProviderOut>(`/providers/${encodeURIComponent(id)}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.providers }),
  });
}

export function useSetProviderCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, apiKey }: { id: string; apiKey: string }) =>
      api.put<ProviderOut>(`/providers/${encodeURIComponent(id)}/credential`, { api_key: apiKey }),
    onSuccess: (_provider, variables) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: qk.providers }),
        queryClient.invalidateQueries({ queryKey: qk.providerModels(variables.id) }),
      ]),
  });
}

export function useDeleteProviderCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del<ProviderOut>(`/providers/${encodeURIComponent(id)}/credential`),
    onSuccess: (_provider, id) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: qk.providers }),
        queryClient.invalidateQueries({ queryKey: qk.providerModels(id) }),
      ]),
  });
}

export function useTestProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<ProviderTestResult>(`/providers/${encodeURIComponent(id)}/test`),
    onSuccess: (_result, id) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: qk.providers }),
        queryClient.invalidateQueries({ queryKey: qk.providerModels(id) }),
      ]),
  });
}

export function useProviderModels(id: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.providerModels(id),
    queryFn: async () => (await api.get<{ items: ProviderModel[] }>(`/providers/${encodeURIComponent(id)}/models`)).items,
    enabled,
    retry: false,
  });
}

export function useProviderUsage(days: number) {
  return useQuery({
    queryKey: qk.providerUsage(days),
    queryFn: () => api.get<ProviderUsage>(`/providers/usage?days=${days}`),
  });
}

// ---------------------------------------------------------------- conversations and chat

export function useConversationsQuery(params: ConversationListParams) {
  return useQuery({
    queryKey: qk.conversationList(params),
    queryFn: () =>
      api.get<Page<ConversationOut>>(`/conversations${buildQuery({ query: params.query, archived: params.archived, limit: 100 })}`),
    placeholderData: keepPreviousData,
  });
}

export function useConversationQuery(id: string | undefined) {
  return useQuery({
    queryKey: qk.conversation(id ?? ""),
    queryFn: () => api.get<ConversationDetail>(`/conversations/${encodeURIComponent(id ?? "")}?limit=200`),
    enabled: Boolean(id),
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => api.post<ConversationOut>("/conversations", title ? { title } : {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations", "list"] }),
  });
}

export interface ConversationPatch {
  title?: string;
  archived?: boolean;
  pinned?: boolean;
}

/** Global search. Runs only for two or more characters, so the palette does not query on every key. */
export function useGlobalSearch(query: string) {
  const text = query.trim();
  return useQuery({
    queryKey: ["search", text],
    queryFn: () => api.get<SearchResult>(`/search${buildQuery({ q: text, limit: 5 })}`),
    enabled: text.length >= 2,
    staleTime: 30_000,
  });
}

export interface MessageFeedbackVariables {
  conversationId: string;
  messageId: string;
  rating: "up" | "down" | null;
}

/** Stores or clears a thumbs rating on a finished assistant reply. The conversation is refetched so the buttons match. */
export function useMessageFeedback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, messageId, rating }: MessageFeedbackVariables) =>
      api.put<MessageOut>(
        `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`,
        { rating },
      ),
    onSuccess: (_message, variables) => queryClient.invalidateQueries({ queryKey: qk.conversation(variables.conversationId) }),
  });
}

export function useUpdateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ConversationPatch }) =>
      api.patch<ConversationOut>(`/conversations/${encodeURIComponent(id)}`, patch),
    onSuccess: (_conversation, variables) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations", "list"] }),
        queryClient.invalidateQueries({ queryKey: qk.conversation(variables.id) }),
      ]),
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/conversations/${encodeURIComponent(id)}`),
    onSuccess: (_result, id) => {
      queryClient.removeQueries({ queryKey: qk.conversation(id) });
      return queryClient.invalidateQueries({ queryKey: ["conversations", "list"] });
    },
  });
}

export function useEditUserMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, messageId, content }: { conversationId: string; messageId: string; content: string }) =>
      api.patch<MessageOut>(
        `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}`,
        { content },
      ),
    onSuccess: (_message, variables) => queryClient.invalidateQueries({ queryKey: qk.conversation(variables.conversationId) }),
  });
}

// ---------------------------------------------------------------- tasks

export function useTasksQuery(params: TaskListParams) {
  return useQuery({
    queryKey: qk.taskList(params),
    queryFn: () =>
      api.get<TaskListResponse>(
        `/tasks${buildQuery({
          status: params.status && params.status !== "all" ? params.status : undefined,
          q: params.q,
          limit: params.limit ?? 50,
          offset: params.offset,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useTaskCountsQuery() {
  return useQuery({
    queryKey: qk.taskCounts,
    queryFn: () => api.get<TaskListResponse>("/tasks?limit=1"),
    select: (response) => response.counts,
  });
}

export function useTaskQuery(id: string | undefined) {
  return useQuery({
    queryKey: qk.task(id ?? ""),
    queryFn: () => api.get<TaskDetailResponse>(`/tasks/${encodeURIComponent(id ?? "")}`),
    enabled: Boolean(id),
  });
}

export type TaskActionName = "approve" | "deny" | "cancel" | "retry";

export function useTaskAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, reason }: { id: string; action: TaskActionName; reason?: string }) =>
      api.post<TaskOut>(`/tasks/${encodeURIComponent(id)}/${action}`, action === "deny" && reason ? { reason } : undefined),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.tasks }),
  });
}

export interface CreateTaskBody {
  tool_name: string;
  arguments: Record<string, unknown>;
  device_id?: string;
  title?: string;
  description?: string;
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateTaskBody) => api.post<TaskOut>("/tasks", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.tasks }),
  });
}

// ---------------------------------------------------------------- devices

export function useDevicesQuery() {
  return useQuery({
    queryKey: qk.devices,
    queryFn: async () => (await api.get<{ items: DeviceOut[] }>("/devices")).items,
  });
}

export function useCreatePairingCode() {
  return useMutation({
    mutationFn: () => api.post<PairingCodeOut>("/devices/pairing-codes"),
  });
}

export interface DevicePatch {
  name?: string;
  device_type?: string;
  is_default?: boolean;
  permissions?: Record<string, { mode: PermissionMode }>;
}

export function useUpdateDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: DevicePatch }) => api.patch<DeviceOut>(`/devices/${encodeURIComponent(id)}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.devices }),
  });
}

export function usePingDevice() {
  return useMutation({
    mutationFn: (id: string) => api.post<PingResult>(`/devices/${encodeURIComponent(id)}/ping`),
  });
}

export function useRevokeDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      api.post<DeviceOut>(`/devices/${encodeURIComponent(id)}/revoke`, reason ? { reason } : {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.devices }),
  });
}

export function useDeleteDevice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/devices/${encodeURIComponent(id)}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.devices }),
  });
}

// ---------------------------------------------------------------- tools, schedules, audit

export function useToolsQuery() {
  return useQuery({
    queryKey: qk.tools,
    queryFn: async () => (await api.get<{ items: ToolOut[] }>("/tools")).items,
    staleTime: 60_000,
  });
}

export function useSchedulesQuery() {
  return useQuery({
    queryKey: qk.schedules,
    queryFn: async () => (await api.get<{ items: ScheduleOut[] }>("/schedules")).items,
  });
}

export function useAuditQuery(limit: number) {
  return useQuery({
    queryKey: qk.audit(limit),
    queryFn: () => api.get<AuditResponse>(`/audit${buildQuery({ limit, offset: 0 })}`),
  });
}

