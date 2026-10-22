import { useState, type FormEvent } from "react";
import { z } from "zod";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input, Select } from "../../components/ui/Input";
import { StatusDot } from "../../components/ui/StatusDot";
import { Switch } from "../../components/ui/Switch";
import { ApiError } from "../../services/api";
import { formatDateTime, humanize } from "../../services/format";
import {
  useDeleteProviderCredential,
  useProviderModels,
  useSetProviderCredential,
  useTestProvider,
  useUpdateProvider,
} from "../../services/queries";
import type { ProviderOut, ProviderTestResult } from "../../types/api";
import { RETRY_RANGE, STATUS_PRESENTATION, TIMEOUT_RANGE, capabilityList, credentialHintText, parseTuning } from "./providerModel";

const credentialSchema = z.object({
  api_key: z.string().trim().min(1, "Enter an API key.").max(512, "That key is too long."),
});

export interface ProviderCardProps {
  provider: ProviderOut;
}

/**
 * One AI provider: enablement, credential, connection test, model choice, timeouts and status.
 * The credential input is never filled from the server. The API returns only a hint.
 */
export function ProviderCard({ provider }: ProviderCardProps) {
  const update = useUpdateProvider();
  const setCredential = useSetProviderCredential();
  const deleteCredential = useDeleteProviderCredential();
  const test = useTestProvider();
  const models = useProviderModels(provider.id, provider.configured);

  const [apiKey, setApiKey] = useState("");
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyMessage, setKeyMessage] = useState<string | null>(null);
  const [timeout, setTimeoutValue] = useState(String(provider.timeout_seconds));
  const [retries, setRetries] = useState(String(provider.max_retries));
  const [tuningMessage, setTuningMessage] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const status = STATUS_PRESENTATION[provider.status];
  const tuning = parseTuning({ timeout, retries });
  const capabilities = capabilityList(provider.capabilities);

  const saveCredential = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setKeyMessage(null);
    const parsed = credentialSchema.safeParse({ api_key: apiKey });
    if (!parsed.success) {
      setKeyError(parsed.error.issues[0]?.message ?? "Enter an API key.");
      return;
    }
    setKeyError(null);
    try {
      await setCredential.mutateAsync({ id: provider.id, apiKey: parsed.data.api_key });
      setApiKey("");
      setKeyMessage("Key saved. Run a connection test to confirm it works.");
    } catch (error) {
      setKeyError(error instanceof ApiError ? error.message : "The key could not be saved.");
    }
  };

  const saveTuning = (): void => {
    setTuningMessage(null);
    if (tuning.timeout_seconds === undefined || tuning.max_retries === undefined) return;
    update.mutate(
      { id: provider.id, patch: { timeout_seconds: tuning.timeout_seconds, max_retries: tuning.max_retries } },
      {
        onSuccess: () => setTuningMessage("Timeout and retries saved."),
        onError: (error) => setTuningMessage(error.message),
      },
    );
  };

  return (
    <Card className="flex min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-ink">{provider.display_name}</h2>
          <p className="font-mono text-xs text-muted">{provider.id}</p>
        </div>
        <StatusDot tone={status.tone} label={status.label} />
      </header>

      <Switch
        label="Enabled"
        description="A disabled provider is never used, including as the fallback."
        checked={provider.enabled}
        disabled={update.isPending}
        onCheckedChange={(enabled) => update.mutate({ id: provider.id, patch: { enabled } })}
      />

      <section aria-labelledby={`${provider.id}-credential`} className="flex flex-col gap-3">
        <h3 id={`${provider.id}-credential`} className="text-sm font-semibold text-ink">
          Credential
        </h3>
        <form className="flex flex-col gap-3" onSubmit={(event) => void saveCredential(event)} noValidate>
          <Field label="API key" hint={credentialHintText(provider)} error={keyError}>
            {(control) => (
              <Input
                {...control}
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={apiKey}
                placeholder={provider.configured ? "Enter a new key to replace the stored one" : "Paste the API key"}
                onChange={(event) => setApiKey(event.target.value)}
              />
            )}
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" variant="primary" loading={setCredential.isPending}>
              Save key
            </Button>
            {provider.configured ? (
              <Button size="sm" variant="danger" onClick={() => setConfirmRemove(true)}>
                Remove key
              </Button>
            ) : null}
          </div>
          {keyMessage ? <p role="status" className="text-sm text-muted">{keyMessage}</p> : null}
        </form>
      </section>

      <section aria-labelledby={`${provider.id}-test`} className="flex flex-col gap-3">
        <h3 id={`${provider.id}-test`} className="text-sm font-semibold text-ink">
          Connection
        </h3>
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="secondary" disabled={!provider.configured} loading={test.isPending} onClick={() => test.mutate(provider.id, { onSuccess: setTestResult, onError: (error) => setTestResult({ ok: false, latency_ms: null, message: error.message, error_code: error instanceof ApiError ? error.code : "test_failed", account_label: null, provider: null }) })}>
            Test connection
          </Button>
          {!provider.configured ? <span className="text-xs text-muted">Save a key to test.</span> : null}
        </div>
        {testResult ? (
          <p role="status" className={testResult.ok ? "text-sm text-success" : "text-sm text-danger"}>
            {testResult.ok ? "Connection works" : "Connection failed"}
            {testResult.latency_ms !== null ? `, ${testResult.latency_ms} ms` : ""}
            {testResult.account_label ? `, account ${testResult.account_label}` : ""}. {testResult.message}
            {testResult.error_code ? ` (${testResult.error_code})` : ""}
          </p>
        ) : null}
      </section>

      <section aria-labelledby={`${provider.id}-model`} className="flex flex-col gap-2">
        <h3 id={`${provider.id}-model`} className="text-sm font-semibold text-ink">
          Model
        </h3>
        {!provider.configured ? (
          <p className="text-sm text-muted">Models load after a key is saved.</p>
        ) : models.isPending ? (
          <p role="status" className="text-sm text-muted">Loading models…</p>
        ) : models.isError ? (
          <p role="alert" className="text-sm text-danger">{models.error.message}</p>
        ) : (
          <Field label="Selected model">
            {(control) => (
              <Select
                {...control}
                value={provider.selected_model ?? ""}
                onChange={(event) => update.mutate({ id: provider.id, patch: { selected_model: event.target.value || null } })}
              >
                <option value="">No model selected</option>
                {(models.data ?? []).map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.display_name} {model.supports_tools ? "(tools)" : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
      </section>

      <section aria-labelledby={`${provider.id}-limits`} className="flex flex-col gap-3">
        <h3 id={`${provider.id}-limits`} className="text-sm font-semibold text-ink">
          Timeouts and retries
        </h3>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Timeout (seconds)" error={tuning.errors.timeout} hint={`${TIMEOUT_RANGE.min} to ${TIMEOUT_RANGE.max}`}>
            {(control) => <Input {...control} type="number" inputMode="numeric" min={TIMEOUT_RANGE.min} max={TIMEOUT_RANGE.max} value={timeout} onChange={(event) => setTimeoutValue(event.target.value)} />}
          </Field>
          <Field label="Retries" error={tuning.errors.retries} hint={`${RETRY_RANGE.min} to ${RETRY_RANGE.max}`}>
            {(control) => <Input {...control} type="number" inputMode="numeric" min={RETRY_RANGE.min} max={RETRY_RANGE.max} value={retries} onChange={(event) => setRetries(event.target.value)} />}
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" disabled={Object.keys(tuning.errors).length > 0} loading={update.isPending} onClick={saveTuning}>
            Save timeouts
          </Button>
          {tuningMessage ? <span role="status" className="text-xs text-muted">{tuningMessage}</span> : null}
        </div>
      </section>

      <section aria-labelledby={`${provider.id}-capabilities`} className="flex flex-col gap-2">
        <h3 id={`${provider.id}-capabilities`} className="text-sm font-semibold text-ink">
          Capabilities
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {capabilities.length === 0 ? <span className="text-sm text-muted">None reported</span> : capabilities.map((label) => <Badge key={label}>{label}</Badge>)}
        </div>
      </section>

      <section aria-labelledby={`${provider.id}-diagnostics`} className="flex flex-col gap-1 rounded-lg border border-line bg-raised p-3 text-sm">
        <h3 id={`${provider.id}-diagnostics`} className="font-semibold text-ink">
          Diagnostics
        </h3>
        <p className="text-muted">Last tested: {formatDateTime(provider.last_tested_at)}</p>
        {provider.last_error ? (
          <p className="text-danger">
            Last error {provider.last_error.code}: {provider.last_error.message}
          </p>
        ) : (
          <p className="text-muted">No error recorded.</p>
        )}
        <p className="text-muted">Status: {humanize(provider.status)}</p>
      </section>

      <Dialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        title={`Remove the ${provider.display_name} key?`}
        description="JARVIS will stop using this provider until a new key is saved."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmRemove(false)}>
              Keep key
            </Button>
            <Button
              variant="danger"
              loading={deleteCredential.isPending}
              onClick={() =>
                deleteCredential.mutate(provider.id, {
                  onSuccess: () => setConfirmRemove(false),
                  onError: (error) => setKeyError(error.message),
                })
              }
            >
              Remove key
            </Button>
          </>
        }
      >
        <span className="sr-only">This cannot be undone. You will need to paste the key again.</span>
      </Dialog>
    </Card>
  );
}
