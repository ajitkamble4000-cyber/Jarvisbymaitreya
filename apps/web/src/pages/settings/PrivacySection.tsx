import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useToast } from "../../components/ui/Toast";
import { api, ApiError } from "../../services/api";
import { downloadAttachment } from "../../services/exportDownload";
import type { PrivacySettings } from "../../types/api";
import { ImmediateSwitch, SettingsCard } from "./SettingsShared";
import { parseBoundedInteger } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";

export const CLEAR_CONFIRMATION = "DELETE";

interface ClearResult {
  matched: number;
  deleted: number;
  dry_run: boolean;
}

/**
 * Deletes conversations older than a number of days. It counts first. Pinned conversations are never deleted.
 * The delete button stays disabled until the word DELETE is typed, and the server counts again on the delete.
 */
function ClearOldConversations() {
  const { toast } = useToast();
  const [days, setDays] = useState("90");
  const [matched, setMatched] = useState<number | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parsedDays = parseBoundedInteger(days, 1, 3650, "number of days");

  const send = async (dryRun: boolean): Promise<ClearResult | null> => {
    if (parsedDays.value === null) return null;
    setBusy(true);
    setError(null);
    try {
      return await api.post<ClearResult>("/conversations/clear", { days: parsedDays.value, dry_run: dryRun });
    } catch (cause) {
      setError(cause instanceof ApiError || cause instanceof Error ? cause.message : "The conversations could not be cleared.");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const count = async (): Promise<void> => {
    const result = await send(true);
    if (result) {
      setMatched(result.matched);
      setTyped("");
    }
  };

  const clear = async (): Promise<void> => {
    if (typed !== CLEAR_CONFIRMATION) return;
    const result = await send(false);
    if (result) {
      toast({ tone: "success", title: "Old conversations deleted", description: `${result.deleted} conversation${result.deleted === 1 ? "" : "s"} removed.` });
      setMatched(null);
      setTyped("");
    }
  };

  return (
    <SettingsCard
      title="Clear old conversations"
      description="Delete conversations that have not changed for a number of days. Pinned conversations are kept. This cannot be undone."
    >
      <Field label="Older than (days)" hint="1 to 3650." error={parsedDays.error}>
        {(control) => (
          <Input
            {...control}
            type="number"
            min={1}
            max={3650}
            step={1}
            invalid={Boolean(parsedDays.error)}
            value={days}
            onChange={(event) => {
              setDays(event.target.value);
              setMatched(null);
            }}
          />
        )}
      </Field>
      <div>
        <Button variant="secondary" loading={busy} disabled={busy || parsedDays.value === null} onClick={() => void count()}>
          Count them
        </Button>
      </div>
      {matched !== null ? (
        <div className="flex flex-col gap-3 rounded-lg border border-line bg-raised p-3">
          <p className="text-sm text-ink" role="status">
            {matched === 0
              ? `No conversations are older than ${days} days.`
              : `${matched} conversation${matched === 1 ? "" : "s"} ${matched === 1 ? "is" : "are"} older than ${days} days and not pinned.`}
          </p>
          {matched > 0 ? (
            <>
              <Field label={`Type ${CLEAR_CONFIRMATION} to confirm`}>
                {(control) => <Input {...control} autoComplete="off" spellCheck={false} value={typed} onChange={(event) => setTyped(event.target.value)} />}
              </Field>
              <div>
                <Button variant="danger" loading={busy} disabled={busy || typed !== CLEAR_CONFIRMATION} onClick={() => void clear()}>
                  Delete {matched} conversation{matched === 1 ? "" : "s"}
                </Button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
    </SettingsCard>
  );
}



export const ACCOUNT_EXPORT_FILE = "jarvis-account-export.json";

/** Settings that decide what JARVIS keeps, and a way for the account holder to take a copy of their data. */
export function PrivacySection({ privacy }: { privacy: PrivacySettings }) {
  const save = useSaveSettings();
  const { toast } = useToast();
  const [downloading, setDownloading] = useState(false);

  const download = async (): Promise<void> => {
    setDownloading(true);
    try {
      const name = await downloadAttachment("/account/export", ACCOUNT_EXPORT_FILE);
      toast({ tone: "success", title: "Your data file is saved", description: name });
    } catch (error) {
      toast({ tone: "error", title: "Your data was not exported", description: error instanceof Error ? error.message : "Try again." });
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Privacy" description="What JARVIS keeps on this account.">
        <ImmediateSwitch
          label="Store conversations"
          description="When on, your chats are saved so that you can return to them. When off, new messages are not kept after the reply."
          checked={privacy.store_conversations}
          pending={save.isPending}
          onChange={(value) => save.mutate({ privacy: { store_conversations: value } })}
        />
      </SettingsCard>

      <ClearOldConversations />

      <SettingsCard
        title="Download my data"
        description="A JSON file with your profile, settings, conversations, memories, tasks, device labels, integration status, schedules, notifications and audit trail."
      >
        <p className="text-sm text-muted">
          The file never includes passwords, provider keys, session or device secrets, or integration tokens. Keep it somewhere private: it holds your conversations.
        </p>
        <div>
          <Button variant="secondary" loading={downloading} disabled={downloading} onClick={() => void download()}>
            Download my data
          </Button>
        </div>
      </SettingsCard>
    </div>
  );
}
