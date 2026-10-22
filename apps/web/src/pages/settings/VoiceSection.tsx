import { useEffect, useState } from "react";
import { Field } from "../../components/ui/Field";
import { Input, Select } from "../../components/ui/Input";
import { detectVoiceCapabilities } from "../../services/speech";
import type { VoiceSettings } from "../../types/api";
import { ImmediateSwitch, SaveBar, SettingsCard } from "./SettingsShared";
import { changedKeys, validateVoiceFields, type FieldErrors } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";

const LANGUAGE_SUGGESTIONS = ["en-US", "en-GB", "en-AU", "en-IN", "fr-FR", "de-DE", "es-ES", "it-IT", "pt-BR", "ja-JP"];
const DEFAULT_VOICE = "";

function readVoiceNames(): string[] {
  try {
    if (typeof window === "undefined" || !window.speechSynthesis) return [];
    return window.speechSynthesis.getVoices().map((voice) => voice.name);
  } catch {
    return [];
  }
}

/** Voice names the browser offers. Empty when the browser cannot list voices. */
function useBrowserVoices(): string[] {
  const [names, setNames] = useState<string[]>(() => readVoiceNames());
  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return undefined;
    const update = (): void => setNames(readVoiceNames());
    window.speechSynthesis.addEventListener?.("voiceschanged", update);
    return () => window.speechSynthesis.removeEventListener?.("voiceschanged", update);
  }, []);
  return names;
}

export function VoiceSection({ voice }: { voice: VoiceSettings }) {
  return <VoiceForm key={JSON.stringify(voice)} voice={voice} />;
}

function VoiceForm({ voice }: { voice: VoiceSettings }) {
  const save = useSaveSettings();
  const capabilities = detectVoiceCapabilities();
  const browserVoices = useBrowserVoices();
  const [draft, setDraft] = useState({
    language: voice.language,
    voice_name: voice.voice_name ?? DEFAULT_VOICE,
    speaking_rate: String(voice.speaking_rate),
    pitch: String(voice.pitch),
  });
  const [errors, setErrors] = useState<FieldErrors>({});

  const voiceOptions = Array.from(new Set([...browserVoices, ...(voice.voice_name ? [voice.voice_name] : [])]));
  const rate = Number(draft.speaking_rate);
  const pitch = Number(draft.pitch);

  const serverTyped = {
    language: voice.language,
    voice_name: voice.voice_name ?? DEFAULT_VOICE,
    speaking_rate: voice.speaking_rate,
    pitch: voice.pitch,
  };
  const changes = changedKeys(serverTyped, { ...draft, speaking_rate: rate, pitch });
  const dirty = Object.keys(changes).length > 0;

  const onSave = (): void => {
    const found = validateVoiceFields({ language: draft.language, speakingRate: rate, pitch });
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const patch: Partial<VoiceSettings> = {};
    if ("language" in changes) patch.language = draft.language.trim();
    if ("voice_name" in changes) patch.voice_name = draft.voice_name || null;
    if ("speaking_rate" in changes) patch.speaking_rate = rate;
    if ("pitch" in changes) patch.pitch = pitch;
    save.mutate({ voice: patch });
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Speech" description="How JARVIS sounds when it speaks. Voices depend on what this browser provides.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Language" hint="A language tag such as en-US." error={errors.language ?? null}>
            {(control) => (
              <>
                <Input
                  {...control}
                  list="voice-language-options"
                  autoComplete="off"
                  invalid={Boolean(errors.language)}
                  value={draft.language}
                  onChange={(event) => setDraft({ ...draft, language: event.target.value })}
                />
                <datalist id="voice-language-options">
                  {LANGUAGE_SUGGESTIONS.map((tag) => (
                    <option key={tag} value={tag} />
                  ))}
                </datalist>
              </>
            )}
          </Field>
          {capabilities.voices ? (
            <Field label="Voice" hint="Browser default uses whatever voice the browser picks for the language.">
              {(control) => (
                <Select {...control} value={draft.voice_name} onChange={(event) => setDraft({ ...draft, voice_name: event.target.value })}>
                  <option value={DEFAULT_VOICE}>Browser default</option>
                  {voiceOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          ) : (
            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium text-ink">Voice</span>
              <span className="text-xs text-muted">This browser cannot list voices, so the browser default is used.</span>
            </div>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={`Speaking rate: ${Number.isFinite(rate) ? rate.toFixed(1) : "?"}`} error={errors.speakingRate ?? null}>
            {(control) => (
              <input
                {...control}
                type="range"
                min={0.5}
                max={2}
                step={0.1}
                value={draft.speaking_rate}
                onChange={(event) => setDraft({ ...draft, speaking_rate: event.target.value })}
                className="w-full accent-[var(--accent)]"
              />
            )}
          </Field>
          <Field label={`Pitch: ${Number.isFinite(pitch) ? pitch.toFixed(1) : "?"}`} error={errors.pitch ?? null}>
            {(control) => (
              <input
                {...control}
                type="range"
                min={0.5}
                max={2}
                step={0.1}
                value={draft.pitch}
                onChange={(event) => setDraft({ ...draft, pitch: event.target.value })}
                className="w-full accent-[var(--accent)]"
              />
            )}
          </Field>
        </div>

        <SaveBar
          dirty={dirty}
          pending={save.isPending}
          error={save.isError ? save.error.message : null}
          onSave={onSave}
          onDiscard={() => {
            setDraft({
              language: voice.language,
              voice_name: voice.voice_name ?? DEFAULT_VOICE,
              speaking_rate: String(voice.speaking_rate),
              pitch: String(voice.pitch),
            });
            setErrors({});
          }}
        />
      </SettingsCard>

      <SettingsCard title="Behaviour" description="Saved as soon as you change them.">
        <ImmediateSwitch
          label="Show captions"
          checked={voice.captions_enabled}
          pending={save.isPending}
          onChange={(value) => save.mutate({ voice: { captions_enabled: value } })}
        />
        <ImmediateSwitch
          label="Speak replies automatically"
          checked={voice.auto_speak}
          pending={save.isPending}
          onChange={(value) => save.mutate({ voice: { auto_speak: value } })}
        />
        <ImmediateSwitch
          label="Push to talk"
          description="On: hold the microphone button to talk. Off: press once to start listening and again to stop."
          checked={voice.push_to_talk}
          pending={save.isPending}
          onChange={(value) => save.mutate({ voice: { push_to_talk: value } })}
        />
        <ImmediateSwitch
          label="Allow interruptions"
          description="Stop a spoken reply as soon as you start talking."
          checked={voice.interruptions_enabled}
          pending={save.isPending}
          onChange={(value) => save.mutate({ voice: { interruptions_enabled: value } })}
        />
        <ImmediateSwitch
          label="Keep listening"
          description={
            capabilities.recognition
              ? "Restart listening after each sentence while the Voice page is open and the microphone is on."
              : "This browser cannot listen, so it has no effect here."
          }
          checked={voice.continuous_listening}
          pending={save.isPending}
          onChange={(value) => save.mutate({ voice: { continuous_listening: value } })}
        />
        <ImmediateSwitch
          label="Hey JARVIS"
          description={
            capabilities.recognition
              ? "Say “Hey JARVIS” and your request from any page. Works in Chrome and Edge while JARVIS is open. The browser sends the audio to its speech service."
              : "This browser cannot listen, so it has no effect here."
          }
          checked={voice.wake_word_enabled}
          pending={save.isPending}
          onChange={(value) => save.mutate({ voice: { wake_word_enabled: value } })}
        />
      </SettingsCard>
    </div>
  );
}
