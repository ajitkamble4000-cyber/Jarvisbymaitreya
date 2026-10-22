import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { useToast } from "../../components/ui/Toast";
import { ApiError } from "../../services/api";
import { downloadConversationExport, type ExportFormat } from "../../services/exportDownload";

const LABELS: Record<ExportFormat, string> = { markdown: "Export Markdown", json: "Export JSON" };

/** Saves the conversation as Markdown or JSON. One download runs at a time, and each outcome is reported in a toast. */
export function ExportButtons({ conversationId }: { conversationId: string }) {
  const { toast } = useToast();
  const [pending, setPending] = useState<ExportFormat | null>(null);

  const run = async (format: ExportFormat): Promise<void> => {
    setPending(format);
    try {
      const fileName = await downloadConversationExport(conversationId, format);
      toast({ tone: "success", title: "Export saved", description: fileName });
    } catch (error) {
      const message = error instanceof ApiError || error instanceof Error ? error.message : "The export could not be made.";
      toast({ tone: "error", title: "Export failed", description: message });
    } finally {
      setPending(null);
    }
  };

  return (
    <div role="group" aria-label="Export this conversation" className="flex flex-wrap gap-2">
      {(Object.keys(LABELS) as ExportFormat[]).map((format) => (
        <Button
          key={format}
          size="sm"
          variant="secondary"
          loading={pending === format}
          disabled={pending !== null}
          onClick={() => void run(format)}
        >
          {LABELS[format]}
        </Button>
      ))}
    </div>
  );
}
