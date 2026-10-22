/**
 * Saves settings through PATCH /settings. Success and failure are announced with a toast, which
 * sits in a polite live region. A successful save replaces the cached document with the server's
 * copy, so every form reloads from the server.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../../components/ui/Toast";
import { api } from "../../services/api";
import { qk } from "../../services/queries";
import type { SettingsDoc, SettingsPatch } from "../../types/api";

export function useSaveSettings() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.patch<SettingsDoc>("/settings", patch),
    onSuccess: (doc) => {
      queryClient.setQueryData(qk.settings, doc);
      toast({ tone: "success", title: "Settings saved" });
    },
    onError: (cause) => {
      toast({ tone: "error", title: "Settings not saved", description: cause.message });
    },
  });
}
