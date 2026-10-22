/** Memory endpoints used by the Memory page (docs/api.md "Memory"). */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, buildQuery } from "../../services/api";
import type { MemoryCategory } from "./memoryModel";
import { MEMORY_CLEAR_PHRASE, MEMORY_PAGE_SIZE } from "./memoryModel";

export interface MemoryItem {
  id: string;
  category: string;
  content: string;
  source: string;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
}

export interface MemoryListResponse {
  items: MemoryItem[];
  total: number;
  enabled: boolean;
  /** "semantic" when the results are ranked by embeddings. "keyword" otherwise. Always present. */
  search_mode: "semantic" | "keyword";
  /** Set only when a semantic request was answered by keyword search. See memoryModel.fallbackReasonText. */
  fallback_reason: string | null;
}

/** The result of POST /memories/reindex. Call again while remaining is above zero. */
export interface ReindexResult {
  updated: number;
  remaining: number;
}

export interface MemoryHistoryItem {
  action: string;
  previous_content: string | null;
  new_content: string | null;
  created_at: string;
}

export interface MemoryListParams {
  query: string;
  category: MemoryCategory | "all";
  offset: number;
  /** Search by meaning (semantic=true). Off sends no parameter, which the API reads as keyword search. */
  semantic: boolean;
}

/** Every memory key starts with "memories", so one invalidation covers the list and history. */
export const memoriesKey = {
  all: ["memories"] as const,
  list: (params: MemoryListParams) => ["memories", "list", params] as const,
  history: (id: string) => ["memories", "history", id] as const,
};

export function useMemoriesQuery(params: MemoryListParams) {
  return useQuery({
    queryKey: memoriesKey.list(params),
    queryFn: () =>
      api.get<MemoryListResponse>(
        `/memories${buildQuery({
          query: params.query.trim() || undefined,
          category: params.category === "all" ? undefined : params.category,
          semantic: params.semantic ? "true" : undefined,
          limit: MEMORY_PAGE_SIZE,
          offset: params.offset,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useMemoryHistory(id: string | null) {
  return useQuery({
    queryKey: memoriesKey.history(id ?? ""),
    queryFn: async () => (await api.get<{ items: MemoryHistoryItem[] }>(`/memories/${encodeURIComponent(id ?? "")}/history`)).items,
    enabled: id !== null,
  });
}

export interface MemoryInput {
  category: MemoryCategory;
  content: string;
}

export function useCreateMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: MemoryInput) => api.post<MemoryItem>("/memories", input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: memoriesKey.all }),
  });
}

export function useUpdateMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<MemoryInput> }) =>
      api.patch<MemoryItem>(`/memories/${encodeURIComponent(id)}`, input),
    onSuccess: (_item, variables) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: memoriesKey.all }),
        queryClient.invalidateQueries({ queryKey: memoriesKey.history(variables.id) }),
      ]),
  });
}

export function useDeleteMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/memories/${encodeURIComponent(id)}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: memoriesKey.all }),
  });
}

/** Deletes every memory. The phrase is sent so that the server checks the same confirmation. */
export function useClearMemories() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.del<{ deleted: number }>("/memories", { confirm: MEMORY_CLEAR_PHRASE }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: memoriesKey.all }),
  });
}

/**
 * Embeds up to 200 memories that have no search index yet. The list is refreshed after each call,
 * because ranking changes once a memory has a vector. A failed call still reports its partial counts.
 */
export function useReindexMemories() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ReindexResult>("/memories/reindex"),
    onSettled: () => queryClient.invalidateQueries({ queryKey: memoriesKey.all }),
  });
}

export function exportMemories() {
  return api.get<unknown>("/memories/export");
}
