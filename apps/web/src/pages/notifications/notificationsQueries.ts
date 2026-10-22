/** Notification endpoints (docs/api.md "Notifications"). Keys start with "notifications" so live events refresh them. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, buildQuery } from "../../services/api";

export type NotificationSeverity = "info" | "success" | "warning" | "error";

export interface NotificationItem {
  id: string;
  kind: string;
  severity: NotificationSeverity | string;
  title: string;
  body: string;
  related_type: string | null;
  related_id: string | null;
  read: boolean;
  created_at: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  unread_count: number;
  /** Undismissed rows that the notification settings hide. Hiding is not deleting. */
  hidden_by_preferences: number;
}

export const notificationsKey = {
  all: ["notifications"] as const,
  list: (unreadOnly: boolean) => ["notifications", "list", unreadOnly] as const,
};

export function useNotificationsQuery(unreadOnly: boolean) {
  return useQuery({
    queryKey: notificationsKey.list(unreadOnly),
    queryFn: () => api.get<NotificationListResponse>(`/notifications${buildQuery({ unread: unreadOnly ? true : undefined, limit: 100 })}`),
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<NotificationItem>(`/notifications/${encodeURIComponent(id)}/read`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey.all }),
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ updated: number }>("/notifications/read-all"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey.all }),
  });
}

export function useDismissNotification() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/notifications/${encodeURIComponent(id)}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey.all }),
  });
}
