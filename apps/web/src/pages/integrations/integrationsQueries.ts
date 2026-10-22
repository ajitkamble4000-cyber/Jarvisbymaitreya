/** Integration endpoints used by the Integrations page (docs/phase2/integrations.md). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../services/api";
import type { IntegrationItem, IntegrationStatus } from "./integrationsModel";

/** Every integration key starts with "integrations", so one invalidation refreshes the catalogue. */
export const integrationsKey = ["integrations"] as const;

export function useIntegrationsQuery() {
  return useQuery({
    queryKey: integrationsKey,
    queryFn: async () => (await api.get<{ items: IntegrationItem[] }>("/integrations")).items,
  });
}

export interface ConnectResult {
  authorization_url: string;
}

export interface ConnectionTestResult {
  ok: boolean;
  status: IntegrationStatus;
  account_label: string | null;
  message: string;
}

export interface DisconnectResult {
  status: "disconnected";
  provider_revoked: boolean;
  message: string;
}

function encodeProvider(id: string): string {
  return encodeURIComponent(id);
}

/** Asks the server for a sign-in address. The browser navigates to it; it never builds the URL itself. */
export function useConnectIntegration() {
  return useMutation({
    mutationFn: (id: string) => api.post<ConnectResult>(`/integrations/${encodeProvider(id)}/connect`),
  });
}

export function useTestIntegration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<ConnectionTestResult>(`/integrations/${encodeProvider(id)}/test`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: integrationsKey }),
  });
}

export function useDisconnectIntegration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del<DisconnectResult>(`/integrations/${encodeProvider(id)}/connection`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: integrationsKey }),
  });
}
