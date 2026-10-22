import { useCallback, useEffect, useMemo, useState, useRef, type KeyboardEvent, type PointerEvent } from "react";
import { PageHeader } from "../../components/layout/PageHeader";
import { AIOrb } from "../../components/orb/AIOrb";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardTitle } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Icon } from "../../components/ui/Icon";
import { Select } from "../../components/ui/Input";
import { StatusDot } from "../../components/ui/StatusDot";
import { Switch } from "../../components/ui/Switch";
import { Tabs } from "../../components/ui/Tabs";
import { useToast } from "../../components/ui/Toast";
import { useVoice } from "../../hooks/useVoice";
import { ApiError } from "../../services/api";
import { streamConversationMessage } from "../../services/chatStream";
import { qk, useConversationsQuery, useCreateConversation, useSettingsQuery, useUpdateSettings } from "../../services/queries";
import { voiceStore } from "../../stores/voice-store";
import { Composer } from "../chat/Composer";
import { ToolProposalCard } from "../chat/ToolProposalCard";
import type { TaskOut, VoiceSettings } from "../../types/api";
import { useQueryClient } from "@tanstack/react-query";
import { capabilityRows, micButtonLabel, orbStateForVoice, pushModeFor, speakingRateLabel, type PushMode } from "./voiceModel";
import { useVoiceSnapshot } from "../../stores/voice-store";
import { ErrorState } from "../../components/ui/ErrorState";

const VOICE_CONVERSATION_KEY = "jarvis.voiceConversation";

function readVoiceConversation(): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(VOICE_CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function writeVoiceConversation(id: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(VOICE_CONVERSATION_KEY, id);
  } catch {
    // Storage is optional: the next turn simply starts a new conversation.
  }
}

interface MicrophoneOption {
  id: string;
  label: string;
}

/** Lists audio inputs when the browser allows it. Labels can be blank until microphone permission is granted. */
function useMicrophones(enabled: boolean): MicrophoneOption[] {
  const [items, setItems] = useState<MicrophoneOption[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    navigator.mediaDevices
      .enumerateDevices()
      .then((devices) => {
        if (cancelled) return;
        setItems(
          devices
            .filter((device) => device.kind === "audioinput")
            .map((device, index) => ({ id: device.deviceId, label: device.label || `Microphone ${index + 1}` })),
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return items;
}

/**
 * Voice assistant. Browser speech is used only where the browser supports it, and every
 * capability is shown with its real status. Transcripts go through the chat endpoint like typed text.
 */
export default function VoicePage() {
  const settings = useSettingsQuery();
  const updateSettings = useUpdateSettings();
  const createConversation = useCreateConversation();
  const conversations = useConversationsQuery({ query: "", archived: false });
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [reply, setReply] = useState("");
  const [replyError, setReplyError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<TaskOut[]>([]);
  const [busy, setBusy] = useState(false);
  const holdingRef = useRef(false);
  const voiceSettings: VoiceSettings | undefined = settings.data?.voice;
  // settings.voice.push_to_talk picks the mode: true is hold to talk, false is press to toggle.
  const mode: PushMode = pushModeFor(voiceSettings?.push_to_talk);

  const sendTranscript = useCallback(
    async (text: string): Promise<void> => {
      setReplyError(null);
      setProposals([]);
      setBusy(true);
      try {
        let conversationId = readVoiceConversation();
        const knownIds = new Set((conversations.data?.items ?? []).map((item) => item.id));
        if (!conversationId || (conversations.data && !knownIds.has(conversationId))) {
          const created = await createConversation.mutateAsync("Voice conversation");
          conversationId = created.id;
          writeVoiceConversation(conversationId);
        }
        let answer = "";
        const stream = streamConversationMessage(conversationId, { content: text });
        for await (const event of stream) {
          if (event.type === "text_delta") answer += event.text;
          if (event.type === "assistant_done" && event.message.content) answer = event.message.content;
          if (event.type === "tool_proposed") setProposals((current) => [...current, event.task]);
          if (event.type === "error") setReplyError(`${event.message} (${event.code})`);
        }
        setReply(answer);
        await queryClient.invalidateQueries({ queryKey: qk.conversation(conversationId) });
        if (answer && voiceSettings?.auto_speak && voice.speak(answer)) {
          // The synthesis engine reports when speech starts and ends, so the state follows it.
        } else {
          voiceStore.dispatch({ type: "RESPONSE_DONE" });
        }
      } catch (error) {
        const message = error instanceof ApiError ? error.message : "The reply could not be completed.";
        setReplyError(message);
        voiceStore.dispatch({ type: "RESPONSE_DONE" });
      } finally {
        setBusy(false);
      }
    },
    // The controller is stable across renders; the settings and conversations drive the callback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [voiceSettings?.auto_speak, conversations.data, createConversation, queryClient],
  );

  const voice = useVoice({
    lang: voiceSettings?.language ?? "en-US",
    voiceName: voiceSettings?.voice_name ?? null,
    speakingRate: voiceSettings?.speaking_rate ?? 1,
    pitch: voiceSettings?.pitch ?? 1,
    continuous: voiceSettings?.continuous_listening ?? false,
    interruptions: voiceSettings?.interruptions_enabled ?? true,
    onFinalTranscript: (text) => void sendTranscript(text),
  });
  const snapshot = useVoiceSnapshot(voiceStore);
  const microphones = useMicrophones(voice.capabilities.microphoneList);
  const rows = useMemo(
    () =>
      capabilityRows({
        capabilities: voice.capabilities,
        voiceCount: voice.voices.length,
        continuousRequested: voice.continuous,
        recognitionActive: snapshot.recognitionActive,
      }),
    [voice.capabilities, voice.voices.length, voice.continuous, snapshot.recognitionActive],
  );

  const save = (patch: Partial<VoiceSettings>): void => {
    updateSettings.mutate(
      { voice: patch },
      { onError: (error) => toast({ tone: "error", title: "Voice setting not saved", description: error.message }) },
    );
  };

  // Hold mode starts on press and stops on release. Toggle mode uses the native click only, so a
  // key press is never counted twice.
  const pressStart = (event: PointerEvent<HTMLButtonElement>): void => {
    if (mode !== "hold") return;
    event.preventDefault();
    holdingRef.current = true;
    voice.startListening();
  };
  const pressEnd = (): void => {
    if (mode !== "hold" || !holdingRef.current) return;
    holdingRef.current = false;
    voice.stopListening();
  };
  const onKey = (event: KeyboardEvent<HTMLButtonElement>, down: boolean): void => {
    if (mode !== "hold") return;
    if (event.key !== " " && event.key !== "Enter") return;
    event.preventDefault();
    if (down && !holdingRef.current) {
      holdingRef.current = true;
      voice.startListening();
    } else if (!down && holdingRef.current) {
      holdingRef.current = false;
      voice.stopListening();
    }
  };
  const onToggleClick = (): void => {
    if (mode !== "toggle") return;
    if (snapshot.recognitionActive) voice.stopListening();
    else voice.startListening();
  };

  const captionsOn = voiceSettings?.captions_enabled ?? true;
  const testSpeech = (): void => {
    const spoke = voice.speak("Voice output is working.");
    if (!spoke) toast({ tone: "info", title: "Speech output is not available", description: "This browser cannot read text aloud." });
  };

  const micActive = snapshot.recognitionActive;
  const micBlocked = !voice.capabilities.recognition;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Voice" description="Speak to JARVIS. Your words go through the same chat as typed messages, so the same rules and approvals apply." />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Card className="flex flex-col items-center gap-6 py-8">
          <AIOrb state={orbStateForVoice(snapshot.state)} style={settings.data?.appearance.orb_style} intensity={settings.data?.appearance.animation_intensity} />

          <div className="flex flex-col items-center gap-2" aria-live="polite">
            <StatusDot
              tone={micActive ? "accent" : "muted"}
              label={micActive ? "Microphone active" : "Microphone off"}
              pulse={micActive}
            />
            {voice.interim ? <p className="max-w-md text-center text-sm italic text-muted">“{voice.interim}”</p> : null}
            {snapshot.state === "error" && snapshot.error ? (
              <p role="alert" className="max-w-md text-center text-sm font-medium text-danger">
                {snapshot.error}
              </p>
            ) : null}
          </div>

          <Tabs
            label="Push to talk mode"
            value={mode}
            onChange={(id) => save({ push_to_talk: id === "hold" })}
            items={[
              { id: "hold", label: "Hold to talk" },
              { id: "toggle", label: "Press to toggle" },
            ]}
          />

          <Button
            variant="primary"
            size="xl"
            aria-label={micButtonLabel(mode, micActive)}
            aria-pressed={mode === "toggle" ? micActive : undefined}
            disabled={micBlocked || busy}
            onPointerDown={pressStart}
            onPointerUp={pressEnd}
            onPointerLeave={pressEnd}
            onPointerCancel={pressEnd}
            onKeyDown={(event) => onKey(event, true)}
            onKeyUp={(event) => onKey(event, false)}
            onClick={onToggleClick}
          >
            <Icon name="voice" className="size-8" />
          </Button>
          <p className="text-sm text-muted">{micButtonLabel(mode, micActive)}</p>
          {micBlocked ? <p className="text-sm text-warning">Speech input is not available in this browser. Use the text box below.</p> : null}
          {busy ? <p role="status" className="text-sm text-muted">Sending to JARVIS…</p> : null}
        </Card>

        <div className="flex flex-col gap-6">
          <Card className="flex flex-col gap-3">
            <CardTitle>Browser capabilities</CardTitle>
            <ul className="flex flex-col gap-3">
              {rows.map((row) => (
                <li key={row.id} className="flex flex-col gap-0.5 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-ink">{row.label}</span>
                    <Badge tone={row.status === "available" || row.status === "active" ? "success" : row.status === "not_available" ? "neutral" : "warning"}>
                      {row.status === "available" ? "Available" : row.status === "active" ? "Active" : row.status === "inactive" ? "Not active" : row.status === "not_available" ? "Not available" : "Unavailable"}
                    </Badge>
                  </div>
                  <p className="text-muted">{row.detail}</p>
                </li>
              ))}
            </ul>
          </Card>

          <Card className="flex flex-col gap-4">
            <CardTitle>Voice settings</CardTitle>
            {!voiceSettings ? (
              <p className="text-sm text-muted">Loading settings…</p>
            ) : settings.isError ? (
              <ErrorState message={settings.error.message} onRetry={() => void settings.refetch()} />
            ) : (
              <>
                <Switch
                  label="Continuous listening"
                  description="Keeps listening between sentences. Off by default."
                  checked={voiceSettings.continuous_listening}
                  disabled={micBlocked}
                  onCheckedChange={(enabled) => {
                    save({ continuous_listening: enabled });
                    // Turning it on starts the microphone from this click. Turning it off stops the engine at once.
                    if (enabled) voice.startListening();
                    else voice.stopListening();
                  }}
                />
                <Switch
                  label="Stop speaking when I talk"
                  description="Stops a spoken reply as soon as you start talking."
                  checked={voiceSettings.interruptions_enabled}
                  onCheckedChange={(interruptions_enabled) => save({ interruptions_enabled })}
                />
                <Switch label="Read replies aloud" checked={voiceSettings.auto_speak} disabled={!voice.capabilities.synthesis} onCheckedChange={(auto_speak) => save({ auto_speak })} />
                <Switch label="Show captions" checked={captionsOn} onCheckedChange={(captions_enabled) => save({ captions_enabled })} />
                <Field label="Voice" hint={voice.voices.length === 0 ? "This browser lists no voices. The default is used." : undefined}>
                  {(control) => (
                    <Select
                      {...control}
                      value={voiceSettings.voice_name ?? ""}
                      disabled={!voice.capabilities.voices || voice.voices.length === 0}
                      onChange={(event) => save({ voice_name: event.target.value || null })}
                    >
                      <option value="">Browser default</option>
                      {voice.voices.map((item) => (
                        <option key={item.voiceURI} value={item.name}>
                          {item.name} ({item.lang})
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Field label={`Speaking speed (${speakingRateLabel(voiceSettings.speaking_rate)})`}>
                  {(control) => (
                    <input
                      {...control}
                      type="range"
                      min={0.5}
                      max={2}
                      step={0.1}
                      value={voiceSettings.speaking_rate}
                      onChange={(event) => save({ speaking_rate: Number(event.target.value) })}
                      className="w-full accent-[var(--accent)]"
                    />
                  )}
                </Field>
                <Field label="Microphone" hint={voice.capabilities.microphoneList ? undefined : "Microphone selection is not supported in this browser."}>
                  {(control) => (
                    <Select
                      {...control}
                      value={voiceSettings.microphone_device_id ?? ""}
                      disabled={!voice.capabilities.microphoneList}
                      onChange={(event) => save({ microphone_device_id: event.target.value || null })}
                    >
                      <option value="">Browser default</option>
                      {microphones.map((mic) => (
                        <option key={mic.id} value={mic.id}>
                          {mic.label}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Switch label="Wake word" description="Not available. No wake-word engine is bundled." checked={false} disabled onCheckedChange={() => undefined} />
                <div className="flex justify-end">
                  <Button size="sm" variant="secondary" disabled={!voice.capabilities.synthesis} onClick={testSpeech}>
                    Test voice
                  </Button>
                </div>
              </>
            )}
          </Card>
        </div>
      </div>

      {captionsOn ? (
        <Card className="flex flex-col gap-3" aria-label="Captions">
          <CardTitle>Captions</CardTitle>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="text-xs uppercase tracking-wide text-muted">You said</p>
              <p className="min-h-6 text-sm text-ink">{snapshot.lastTranscript || "Nothing yet."}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted">JARVIS replied</p>
              <p className="min-h-6 whitespace-pre-wrap text-sm text-ink">{reply || "Nothing yet."}</p>
            </div>
          </div>
          {replyError ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {replyError}
            </p>
          ) : null}
          {proposals.map((task) => (
            <ToolProposalCard key={task.id} task={task} />
          ))}
        </Card>
      ) : null}

      <Card className="flex flex-col gap-3">
        <CardTitle>Type instead</CardTitle>
        <p className="text-sm text-muted">The text box always works, even when speech is not available.</p>
        <Composer disabled={busy} streaming={false} onStop={() => undefined} onSend={(text) => void sendTranscript(text)} />
      </Card>
    </div>
  );
}
