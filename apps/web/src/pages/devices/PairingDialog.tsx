import { useEffect, useState } from "react";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ErrorState } from "../../components/ui/ErrorState";
import { useNow } from "../../hooks/useNow";
import { formatCountdown } from "../../services/format";
import { useCreatePairingCode } from "../../services/queries";
import type { PairingCodeOut } from "../../types/api";
import { secondsRemaining } from "./deviceModel";

export interface PairingDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Creates a one-time pairing code. The code is held only in this dialog's state. It is cleared
 * when the dialog closes, so it is shown once and never stored or listed afterwards.
 */
export function PairingDialog({ open, onClose }: PairingDialogProps) {
  const create = useCreatePairingCode();
  const [code, setCode] = useState<PairingCodeOut | null>(null);
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const now = useNow(1000);

  useEffect(() => {
    if (!open) {
      setCode(null);
      setCopied("idle");
      create.reset();
    }
  }, [open]);

  const generate = async (): Promise<void> => {
    setCopied("idle");
    try {
      setCode(await create.mutateAsync());
    } catch {
      setCode(null);
    }
  };

  const copy = async (): Promise<void> => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.code);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  };

  const remaining = code ? secondsRemaining(code.expires_at, now) : 0;
  const expired = code !== null && remaining === 0;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Connect a device"
      description="Create a one-time code and enter it in the JARVIS Windows agent on the PC you want to pair."
      footer={
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {create.isError ? <ErrorState message={create.error.message} onRetry={() => void generate()} retryLabel="Create a new code" /> : null}

        {code && !expired ? (
          <div className="flex flex-col items-center gap-3 rounded-card border border-accent/50 bg-accent/10 p-5">
            <p className="text-xs uppercase tracking-wider text-muted">Pairing code</p>
            <p aria-label={`Pairing code ${code.code.split("").join(" ")}`} className="font-mono text-3xl font-semibold tracking-[0.2em] text-ink">
              {code.code}
            </p>
            <p className="text-sm text-muted">
              Expires in <span className="font-mono tabular-nums text-ink">{formatCountdown(remaining)}</span>. The code works once.
            </p>
            <Button size="sm" variant="secondary" onClick={() => void copy()}>
              {copied === "copied" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy code"}
            </Button>
          </div>
        ) : null}

        {expired ? (
          <p role="status" className="text-sm text-warning">
            This code has expired. Create a new one to pair a device.
          </p>
        ) : null}

        {!code || expired ? (
          <div className="flex justify-end">
            <Button variant="primary" loading={create.isPending} onClick={() => void generate()}>
              {expired ? "Create a new code" : "Create pairing code"}
            </Button>
          </div>
        ) : null}

        <p className="text-xs text-muted">
          The code is shown only here. Closing this dialog removes it from the screen, and it cannot be viewed again.
        </p>
      </div>
    </Dialog>
  );
}
