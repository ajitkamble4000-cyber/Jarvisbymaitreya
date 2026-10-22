/** Session and password endpoints (docs/api.md "Auth"). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../services/api";

export interface SessionItem {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string | null;
  ip_address: string | null;
  current: boolean;
}

export const sessionsKey = ["auth", "sessions"] as const;

export function useSessionsQuery() {
  return useQuery({
    queryKey: sessionsKey,
    queryFn: () => api.get<SessionItem[]>("/auth/sessions"),
  });
}

export function useRevokeSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/auth/sessions/${encodeURIComponent(id)}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionsKey }),
  });
}

export interface PasswordChange {
  current_password: string;
  new_password: string;
}

/** Changes the password. The server signs out every other session. */
export function useChangePassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: PasswordChange) => api.post<void>("/auth/password", input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionsKey }),
  });
}
