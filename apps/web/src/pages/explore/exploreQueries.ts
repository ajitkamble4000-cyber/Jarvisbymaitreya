/** GET /explore: what JARVIS can do, with the status of each item. Discovering an item never authorises it. */

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, buildQuery } from "../../services/api";

export type ExploreStatus = "working" | "requires_configuration" | "experimental" | "unsupported";

export interface ExploreItem {
  id: string;
  title: string;
  category: string;
  status: ExploreStatus;
  description: string;
  example_commands: string[];
  requirements: string[];
  enabled_for_user: boolean;
}

export interface ExploreResponse {
  categories: string[];
  items: ExploreItem[];
}

export interface ExploreParams {
  category: string;
  q: string;
}

export function useExploreQuery(params: ExploreParams) {
  return useQuery({
    queryKey: ["explore", params] as const,
    queryFn: () => api.get<ExploreResponse>(`/explore${buildQuery({ category: params.category || undefined, q: params.q || undefined })}`),
    placeholderData: keepPreviousData,
  });
}
