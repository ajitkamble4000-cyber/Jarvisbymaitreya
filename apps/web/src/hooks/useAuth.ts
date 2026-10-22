import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../services/api";
import { clearChatDrafts } from "../services/chatDrafts";
import { qk, useAuthStatusQuery } from "../services/queries";
import type { AuthStatus, UserOut } from "../types/api";

export interface SetupInput {
  email: string;
  password: string;
  display_name?: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

/**
 * Auth state and mutations. After login or setup the auth status is refetched so that the
 * auth gate re-evaluates. Logout clears every cached query except the auth status, so one
 * viewer's data cannot remain in memory for the next viewer of the same browser tab.
 */
export function useAuth() {
  const queryClient = useQueryClient();
  const status = useAuthStatusQuery();

  const login = useMutation({
    mutationFn: (input: LoginInput) => api.post<UserOut>("/auth/login", input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.authStatus }),
  });

  const setup = useMutation({
    mutationFn: (input: SetupInput) => api.post<UserOut>("/auth/setup", input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.authStatus }),
  });

  const logout = useMutation({
    mutationFn: () => api.post<void>("/auth/logout"),
    onSettled: () => {
      clearChatDrafts();
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
      queryClient.setQueryData<AuthStatus>(qk.authStatus, { setup_required: false, authenticated: false, user: null });
    },
  });

  const data = status.data;
  return {
    status: data,
    isLoading: status.isLoading,
    error: status.error,
    retry: () => status.refetch(),
    isAuthenticated: Boolean(data?.authenticated),
    user: data?.user ?? null,
    login,
    setup,
    logout,
  };
}
