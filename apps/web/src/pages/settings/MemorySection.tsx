import { useState } from "react";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Switch } from "../../components/ui/Switch";
import type { MemorySettings } from "../../types/api";
import { ImmediateSwitch, SectionLink, SaveBar, SettingsCard } from "./SettingsShared";
import { validateRetention, type FieldErrors } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";

export function MemorySection({ memory }: { memory: MemorySettings }) {
  return <MemoryForm key={JSON.stringify(memory)} memory={memory} />;
}

function MemoryForm({ memory }: { memory: MemorySettings }) {
  const save = useSaveSettings();
  const [retention, setRetention] = useState(memory.retention_days === null ? "" : String(memory.retention_days));
  const [errors, setErrors] = useState<FieldErrors>({});
  const dirty = retention !== (memory.retention_days === null ? "" : String(memory.retention_days));

  const onSave = (): void => {
    const parsed = validateRetention(retention);
    if (parsed.error) {
      setErrors({ retention: parsed.error });
      return;
    }
    setErrors({});
    save.mutate({ memory: { retention_days: parsed.value } });
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Memory" description="Turn remembering on or off. Items are listed and edited on the Memory page.">
        <Switch
          label="Remember things across conversations"
          description="When off, JARVIS does not add or use memories. Existing items stay until you delete them."
          checked={memory.enabled}
          disabled={save.isPending}
          onCheckedChange={(value) => save.mutate({ memory: { enabled: value } })}
        />
        <SectionLink to="/memory">Open the Memory page</SectionLink>
        <Field
          label="Keep memories for (days)"
          hint="Leave blank to keep them until you delete them. 1 to 3650."
          error={errors.retention ?? null}
        >
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              max={3650}
              invalid={Boolean(errors.retention)}
              value={retention}
              onChange={(event) => setRetention(event.target.value)}
            />
          )}
        </Field>
        <ImmediateSwitch
          label="Save conversation summaries automatically"
          description="After a reply, JARVIS may save a short summary of older messages as a memory. It uses your active AI provider's paid API, one call per summary. It works only while remembering is on."
          checked={memory.auto_save_summaries}
          pending={save.isPending}
          onChange={(value) => save.mutate({ memory: { auto_save_summaries: value } })}
        />
        <SaveBar
          dirty={dirty}
          pending={save.isPending}
          error={save.isError ? save.error.message : null}
          onSave={onSave}
          onDiscard={() => {
            setRetention(memory.retention_days === null ? "" : String(memory.retention_days));
            setErrors({});
          }}
        />
      </SettingsCard>
    </div>
  );
}
