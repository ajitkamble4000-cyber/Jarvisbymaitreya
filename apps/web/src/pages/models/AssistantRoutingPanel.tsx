import { Card, CardTitle } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Input";
import { Switch } from "../../components/ui/Switch";
import { Button } from "../../components/ui/Button";
import { useToast } from "../../components/ui/Toast";
import { useUpdateSettings } from "../../services/queries";
import type { ProviderOut, SettingsDoc } from "../../types/api";
import { useState } from "react";

export interface AssistantRoutingPanelProps {
  settings: SettingsDoc;
  providers: ProviderOut[];
}

/**
 * Chooses the active provider and the fallback. Both are settings.ai values. The fallback never
 * equals the active provider, and it runs only while fallback_enabled is on.
 */
export function AssistantRoutingPanel({ settings, providers }: AssistantRoutingPanelProps) {
  const update = useUpdateSettings();
  const { toast } = useToast();
  const [active, setActive] = useState(settings.ai.active_provider);
  const [fallback, setFallback] = useState(settings.ai.fallback_provider ?? "");
  const [fallbackEnabled, setFallbackEnabled] = useState(settings.ai.fallback_enabled);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    active !== settings.ai.active_provider ||
    (fallback || null) !== settings.ai.fallback_provider ||
    fallbackEnabled !== settings.ai.fallback_enabled;

  const save = (): void => {
    setError(null);
    if (fallback && fallback === active) {
      setError("The fallback must be a different provider from the active one.");
      return;
    }
    update.mutate(
      { ai: { active_provider: active, fallback_provider: fallback || null, fallback_enabled: fallbackEnabled } },
      {
        onSuccess: () => toast({ tone: "success", title: "Provider routing saved" }),
        onError: (cause) => setError(cause.message),
      },
    );
  };

  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Active provider and fallback</CardTitle>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Active provider">
          {(control) => (
            <Select {...control} value={active} onChange={(event) => setActive(event.target.value)}>
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.display_name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Fallback provider" hint="Used only when fallback is switched on and the active provider fails.">
          {(control) => (
            <Select {...control} value={fallback} onChange={(event) => setFallback(event.target.value)}>
              <option value="">No fallback</option>
              {providers
                .filter((provider) => provider.id !== active)
                .map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.display_name}
                  </option>
                ))}
            </Select>
          )}
        </Field>
      </div>
      <Switch
        label="Use the fallback when the active provider fails"
        description="Replies that used the fallback always say so."
        checked={fallbackEnabled}
        onCheckedChange={setFallbackEnabled}
      />
      {error ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button variant="primary" size="sm" disabled={!dirty} loading={update.isPending} onClick={save}>
          Save routing
        </Button>
      </div>
    </Card>
  );
}
