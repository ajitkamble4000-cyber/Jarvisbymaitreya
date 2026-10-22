import { useState } from "react";
import { Field } from "../../components/ui/Field";
import { Input, Select, Textarea } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Switch } from "../../components/ui/Switch";
import { useCallsTodayQuery, useProvidersQuery } from "../../services/queries";
import { humanize } from "../../services/format";
import type { AiSettings, SettingsPatch } from "../../types/api";
import { ImmediateSwitch, SaveBar, SectionLink, SettingsCard } from "./SettingsShared";
import { SYSTEM_INSTRUCTIONS_MAX, callsTodayText, changedKeys, parseCallLimit, validateAiFields, type FieldErrors } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";

/** Draft values for the AI form. Numbers are kept as text while the user types. */
interface AiDraft {
  active_provider: string;
  fallback_provider: string | null;
  fallback_enabled: boolean;
  temperature: string;
  max_output_tokens: string;
  daily_call_limit: string;
  system_instructions: string;
}

function draftFrom(ai: AiSettings): AiDraft {
  return {
    active_provider: ai.active_provider,
    fallback_provider: ai.fallback_provider,
    fallback_enabled: ai.fallback_enabled,
    temperature: String(ai.temperature),
    max_output_tokens: String(ai.max_output_tokens),
    daily_call_limit: ai.daily_call_limit === null ? "" : String(ai.daily_call_limit),
    system_instructions: ai.system_instructions,
  };
}

/** AI settings. The form remounts whenever the server values change, so it always starts from the server copy. */
export function AiSection({ ai }: { ai: AiSettings }) {
  return <AiForm key={JSON.stringify(ai)} ai={ai} />;
}

function AiForm({ ai }: { ai: AiSettings }) {
  const providers = useProvidersQuery();
  const callsToday = useCallsTodayQuery();
  const save = useSaveSettings();
  const [draft, setDraft] = useState<AiDraft>(() => draftFrom(ai));
  const [errors, setErrors] = useState<FieldErrors>({});

  const providerOptions = (providers.data ?? [{ id: ai.active_provider, display_name: humanize(ai.active_provider) }]).map((provider) => ({
    id: provider.id,
    label: provider.display_name,
  }));

  // Only the keys that differ from the server are sent. Numbers are compared as numbers.
  const serverTyped = { ...draftFrom(ai), temperature: ai.temperature, max_output_tokens: ai.max_output_tokens, daily_call_limit: ai.daily_call_limit };
  const limit = parseCallLimit(draft.daily_call_limit);
  const draftTyped = {
    ...draft,
    temperature: Number(draft.temperature),
    max_output_tokens: Number(draft.max_output_tokens),
    daily_call_limit: limit.value,
  };
  const changes = changedKeys(serverTyped, draftTyped);
  const dirty = Object.keys(changes).length > 0;

  const onSave = (): void => {
    const found = validateAiFields({
      temperature: draft.temperature,
      maxOutputTokens: draft.max_output_tokens,
      systemInstructions: draft.system_instructions,
      activeProvider: draft.active_provider,
      fallbackProvider: draft.fallback_provider ?? "",
    });
    if (limit.error) found.dailyCallLimit = limit.error;
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const patch: SettingsPatch["ai"] = changes;
    save.mutate({ ai: patch });
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Provider" description="Which AI service answers, and whether a second one takes over when the first fails.">
        {providers.isPending ? <SkeletonList rows={2} label="Loading providers" /> : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Active provider">
            {(control) => (
              <Select
                {...control}
                value={draft.active_provider}
                onChange={(event) => {
                  setDraft({ ...draft, active_provider: event.target.value });
                  setErrors({});
                }}
              >
                {providerOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Fallback provider" hint="Used only when the fallback is switched on below." error={errors.fallbackProvider ?? null}>
            {(control) => (
              <Select
                {...control}
                invalid={Boolean(errors.fallbackProvider)}
                value={draft.fallback_provider ?? ""}
                onChange={(event) => {
                  setDraft({ ...draft, fallback_provider: event.target.value || null });
                  setErrors({});
                }}
              >
                <option value="">No fallback</option>
                {providerOptions
                  .filter((option) => option.id !== draft.active_provider)
                  .map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
              </Select>
            )}
          </Field>
        </div>
        <Switch
          label="Use the fallback when the active provider fails"
          description="Replies that used the fallback always say so."
          checked={draft.fallback_enabled}
          onCheckedChange={(value) => setDraft({ ...draft, fallback_enabled: value })}
        />
        <p className="text-xs text-muted">
          Timeouts and retries are set per provider on the <SectionLink to="/models">AI Models</SectionLink> page.
        </p>
      </SettingsCard>

      <SettingsCard title="Replies" description="How the model writes. Changes apply to the next message.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Temperature" hint="0 is steady, 2 is most varied." error={errors.temperature ?? null}>
            {(control) => (
              <Input
                {...control}
                type="number"
                step="0.1"
                min={0}
                max={2}
                invalid={Boolean(errors.temperature)}
                value={draft.temperature}
                onChange={(event) => setDraft({ ...draft, temperature: event.target.value })}
              />
            )}
          </Field>
          <Field label="Maximum reply length (tokens)" hint="64 to 8192." error={errors.maxOutputTokens ?? null}>
            {(control) => (
              <Input
                {...control}
                type="number"
                step={1}
                min={64}
                max={8192}
                invalid={Boolean(errors.maxOutputTokens)}
                value={draft.max_output_tokens}
                onChange={(event) => setDraft({ ...draft, max_output_tokens: event.target.value })}
              />
            )}
          </Field>
        </div>
        <Field label="Daily AI call limit" hint="Blank means no limit. 1 to 10000 calls per UTC day. A refused reply makes no call." error={errors.dailyCallLimit ?? null}>
          {(control) => (
            <Input
              {...control}
              type="number"
              step={1}
              min={1}
              max={10000}
              placeholder="No limit"
              invalid={Boolean(errors.dailyCallLimit)}
              value={draft.daily_call_limit}
              onChange={(event) => setDraft({ ...draft, daily_call_limit: event.target.value })}
            />
          )}
        </Field>
        {callsToday.data ? <p className="text-sm text-muted">{callsTodayText(callsToday.data)}</p> : null}
        <Field
          label="System instructions"
          hint={`Sent with every message. ${draft.system_instructions.length} of ${SYSTEM_INSTRUCTIONS_MAX} characters.`}
          error={errors.systemInstructions ?? null}
        >
          {(control) => (
            <Textarea
              {...control}
              rows={4}
              invalid={Boolean(errors.systemInstructions)}
              value={draft.system_instructions}
              onChange={(event) => setDraft({ ...draft, system_instructions: event.target.value })}
            />
          )}
        </Field>
        <ImmediateSwitch
          label="Stream replies as they are written"
          checked={ai.stream_responses}
          pending={save.isPending}
          onChange={(value) => save.mutate({ ai: { stream_responses: value } })}
        />
        <ImmediateSwitch
          label="Include memory in replies"
          description="When on, saved memories are sent with each message. Memory itself is managed on the Memory page."
          checked={ai.include_memory}
          pending={save.isPending}
          onChange={(value) => save.mutate({ ai: { include_memory: value } })}
        />
      </SettingsCard>

      <SaveBar
        dirty={dirty}
        pending={save.isPending}
        error={save.isError ? save.error.message : null}
        onSave={onSave}
        onDiscard={() => {
          setDraft(draftFrom(ai));
          setErrors({});
        }}
      />
    </div>
  );
}
