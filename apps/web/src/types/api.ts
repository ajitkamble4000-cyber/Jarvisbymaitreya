/**
 * Types mirroring docs/api.md. Field names are identical to the API so that responses can be
 * used without mapping. Keep this file in step with the contract; do not rename fields here.
 */

export type IsoDateTime = string;

export interface Page<T> {
  items: T[];
  total: number;
}

// ---------------------------------------------------------------- health and auth

export interface HealthOut {
  status: "ok" | "degraded";
  version: string;
  database: "ok" | "error";
  background_workers: unknown;
  time: IsoDateTime;
}

/** One account as the owner sees it on /users. password_hash is never sent. */
export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: "owner" | "member";
  is_active: boolean;
  created_at: IsoDateTime;
  last_login_at: IsoDateTime | null;
}

/** One search across conversations, memories and tasks. Each group is capped by the server's limit. */
export interface SearchResult {
  query: string;
  conversations: Array<{ id: string; title: string; match: "title" | "message"; updated_at: IsoDateTime }>;
  memories: Array<{ id: string; category: string; excerpt: string }>;
  tasks: Array<{ id: string; title: string; tool_name: string; status: TaskStatus }>;
}

export interface AdminUserList {
  items: AdminUser[];
}

export interface UserOut {
  id: string;
  email: string;
  display_name: string | null;
  role: string;
  created_at: IsoDateTime;
  last_login_at: IsoDateTime | null;
}

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  user?: UserOut | null;
}

// ---------------------------------------------------------------- settings

export interface AiSettings {
  /** Provider calls allowed per UTC day, or null for no limit. A refused reply makes no call. */
  daily_call_limit: number | null;
  active_provider: string;
  fallback_provider: string | null;
  fallback_enabled: boolean;
  temperature: number;
  max_output_tokens: number;
  system_instructions: string;
  stream_responses: boolean;
  include_memory: boolean;
}

/** Provider calls made today (UTC), counted from measured usage. `limit` is null when no daily limit is set. */
export interface CallsToday {
  calls: number;
  limit: number | null;
  resets_at: IsoDateTime;
}

export interface VoiceSettings {
  language: string;
  voice_name: string | null;
  speaking_rate: number;
  pitch: number;
  captions_enabled: boolean;
  auto_speak: boolean;
  interruptions_enabled: boolean;
  push_to_talk: boolean;
  continuous_listening: boolean;
  wake_word_enabled: boolean;
  microphone_device_id: string | null;
  audio_output_device_id: string | null;
}

export type ThemeSetting = "dark" | "light" | "system" | "high-contrast";
export type OrbStyle = "orb" | "ring" | "minimal";
export type AnimationIntensity = "off" | "low" | "normal" | "high";
export type ReducedMotionSetting = "system" | "on" | "off";
export type FontSizeSetting = "small" | "medium" | "large";

export interface DashboardWidgets {
  status: boolean;
  devices: boolean;
  tasks: boolean;
  activity: boolean;
  schedule: boolean;
  quick_actions: boolean;
}

export interface AppearanceSettings {
  theme: ThemeSetting;
  accent_color: string;
  orb_style: OrbStyle;
  animation_intensity: AnimationIntensity;
  reduced_motion: ReducedMotionSetting;
  font_size: FontSizeSetting;
  sidebar_hidden: string[];
  dashboard_widgets: DashboardWidgets;
}

export interface AutomationSettings {
  max_concurrent_tasks: number;
  default_task_timeout_seconds: number;
  disabled_tools: string[];
  emergency_stopped: boolean;
}

export interface NotificationSettings {
  desktop_notifications: boolean;
  on_task_completed: boolean;
  on_task_failed: boolean;
  on_approval_required: boolean;
  on_device_disconnected: boolean;
  on_integration_error: boolean;
  on_schedule_event: boolean;
  on_security_event: boolean;
  include_task_details: boolean;
  /** Pauses browser pop-ups only. The inbox still lists every notification. */
  quiet_hours_enabled: boolean;
  /** "HH:MM" in the reminder time zone, or this browser's zone when none is set. */
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export interface MemorySettings {
  enabled: boolean;
  retention_days: number | null;
  auto_save_summaries: boolean;
}

export interface PrivacySettings {
  store_conversations: boolean;
}

export interface SettingsDoc {
  ai: AiSettings;
  voice: VoiceSettings;
  appearance: AppearanceSettings;
  automation: AutomationSettings;
  notifications: NotificationSettings;
  memory: MemorySettings;
  privacy: PrivacySettings;
  /** IANA time zone for "HH:MM" reminders and schedules. null means UTC. A top-level key, not a section. */
  timezone: string | null;
}

/**
 * A partial settings document: top-level sections with any subset of their keys. `timezone` is a
 * top-level value, so it is sent as itself (a name, or null for UTC) and not as a section.
 */
export type SettingsPatch = {
  [K in Exclude<keyof SettingsDoc, "timezone">]?: Partial<SettingsDoc[K]>;
} & { timezone?: string | null };

// ---------------------------------------------------------------- AI providers

export type ProviderStatus = "not_configured" | "untested" | "connected" | "error";

export interface ProviderCapabilities {
  streaming: boolean;
  tool_calling: boolean;
  multimodal_input: boolean;
  usage_reporting: boolean;
  model_listing: boolean;
}

export interface ProviderError {
  code: string;
  message: string;
}

export interface ProviderOut {
  id: string;
  display_name: string;
  enabled: boolean;
  configured: boolean;
  credential_hint: string | null;
  selected_model: string | null;
  timeout_seconds: number;
  max_retries: number;
  status: ProviderStatus;
  last_tested_at: IsoDateTime | null;
  last_latency_ms: number | null;
  last_error: ProviderError | null;
  capabilities: ProviderCapabilities;
}

export interface ProviderModel {
  id: string;
  display_name: string;
  supports_tools: boolean;
  supports_streaming: boolean;
  input_modalities: string[];
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms: number | null;
  message: string;
  error_code: string | null;
  account_label: string | null;
  provider: ProviderOut | null;
}

export interface UsageTotals {
  requests: number;
  successes: number;
  failures: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  /** true when token counts are estimated rather than reported by the provider. */
  tokens_estimated: boolean;
}

export interface UsageRow {
  provider_id?: string;
  model?: string;
  requests: number;
  failures?: number;
  total_tokens?: number;
  avg_latency_ms?: number | null;
}

export interface ProviderUsage {
  period_days: number;
  generated_at: IsoDateTime;
  totals: UsageTotals;
  avg_latency_ms: number | null;
  by_provider: UsageRow[];
  by_model: UsageRow[];
  error_frequency: Array<{ code: string; count: number }>;
  estimated_cost_usd: number | null;
  cost_source: "measured" | "estimated" | "unavailable" | string;
  cost_note: string;
}

// ---------------------------------------------------------------- conversations and chat

export type MessageRole = "user" | "assistant" | "system" | "tool";
export type MessageStatus = "complete" | "streaming" | "error" | "cancelled";

export interface FallbackUsed {
  provider_id: string;
  reason: string;
}

export interface MessageMeta {
  usage?: Record<string, unknown>;
  fallback_used?: FallbackUsed | null;
  tool_calls?: unknown[];
  latency_ms?: number;
  /** The viewer's thumbs rating on a finished assistant reply. Absent when none was given. */
  feedback?: { rating: "up" | "down"; given_at: string };
}

export interface ConversationOut {
  id: string;
  title: string;
  archived: boolean;
  /** Pinned conversations list first in the same archived state. */
  pinned: boolean;
  provider_id: string | null;
  model: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  last_message_at: IsoDateTime | null;
}

export interface MessageOut {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  provider_id: string | null;
  model: string | null;
  task_id: string | null;
  meta: MessageMeta;
  created_at: IsoDateTime;
  edited_at: IsoDateTime | null;
}

export interface ConversationDetail {
  conversation: ConversationOut;
  messages: MessageOut[];
}

// ---------------------------------------------------------------- tasks

export type TaskStatus = "queued" | "running" | "awaiting_approval" | "completed" | "failed" | "cancelled";

export type TaskDecision = "awaiting_approval" | "queued" | "needs_device_selection" | "rejected";

export interface TaskDevice {
  id: string;
  name: string;
  connection_status: string;
}

export interface TaskError {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface TaskResult {
  summary: string;
  output: unknown;
}

export interface TaskActions {
  approve: boolean;
  deny: boolean;
  cancel: boolean;
  retry: boolean;
}

export interface TaskOut {
  id: string;
  title: string;
  description: string;
  source: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk_level: string;
  status: TaskStatus;
  device: TaskDevice | null;
  conversation_id: string | null;
  required_permissions: string[];
  current_step: string | null;
  progress: { value: number; step: string } | null;
  result: TaskResult | null;
  error: TaskError | null;
  attempt: number;
  max_attempts: number;
  approval_expires_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  actions: TaskActions;
}

export interface TaskCounts {
  queued: number;
  running: number;
  awaiting_approval: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface TaskListResponse extends Page<TaskOut> {
  counts: TaskCounts;
}

export interface TaskEventOut {
  id: string;
  type: string;
  message: string;
  data: Record<string, unknown>;
  created_at: IsoDateTime;
}

export interface TaskDetailResponse {
  task: TaskOut;
  events: TaskEventOut[];
}

// ---------------------------------------------------------------- devices

export type PermissionMode = "ask" | "allow" | "deny";

export interface DevicePermission {
  mode: PermissionMode;
}

export interface DeviceResources {
  apps: Array<{ id: string; label: string }>;
  urls: Array<{ id: string; label: string; url?: string }>;
  folders: Array<{ id: string; label: string }>;
  projects: Array<{ id: string; label: string }>;
  files: Array<{ id: string; label: string }>;
  /** Labels of the approved project checks. Commands never leave the device, so only labels are sent. */
  checks?: Array<{ id: string; label: string }>;
}

export interface DeviceOut {
  id: string;
  name: string;
  device_type: string;
  os: string;
  os_version: string;
  agent_version: string;
  pairing_status: "paired" | "revoked";
  connection_status: "online" | "offline" | "revoked";
  is_default: boolean;
  capabilities: string[];
  permissions: Record<string, DevicePermission>;
  resources: DeviceResources;
  registered_at: IsoDateTime | null;
  paired_at: IsoDateTime | null;
  last_seen_at: IsoDateTime | null;
  last_connected_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
}

export interface PairingCodeOut {
  code: string;
  expires_at: IsoDateTime;
}

export interface PingResult {
  online: boolean;
  latency_ms: number | null;
  message: string;
}

// ---------------------------------------------------------------- tools

export interface ToolOut {
  name: string;
  title: string;
  description: string;
  category: string;
  risk_level: string;
  target: "server" | "device";
  requires_device: boolean;
  approval_required: boolean;
  status: string;
  example_commands: string[];
  notes: string;
  parameters: Record<string, unknown>;
}

// ---------------------------------------------------------------- schedules

export interface ScheduleOut {
  id: string;
  name: string;
  description: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  device_id: string | null;
  schedule: {
    kind: "once" | "daily" | "weekly";
    run_at: IsoDateTime | null;
    time_of_day: string | null;
    weekdays: number[] | null;
    timezone: string;
  };
  enabled: boolean;
  paused: boolean;
  next_run_at: IsoDateTime | null;
  last_run_at: IsoDateTime | null;
  last_status: string | null;
  consecutive_failures: number;
  state: "scheduled" | "paused" | "disabled" | "completed" | "failing";
}

// ---------------------------------------------------------------- audit

export interface AuditEntry {
  id: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  data: Record<string, unknown>;
  created_at: IsoDateTime;
}

export type AuditResponse = Page<AuditEntry>;

// ---------------------------------------------------------------- events stream

export type EventName =
  | "task.updated"
  | "approval.requested"
  | "device.updated"
  | "schedule.updated"
  | "chat.message"
  | "notification.created"
  | "heartbeat";

export interface TaskUpdatedEvent {
  task_id: string;
  status: TaskStatus;
}

export interface ApprovalRequestedEvent {
  task_id: string;
  title: string;
}

export interface DeviceUpdatedEvent {
  device_id: string;
  connection_status: string;
}

export interface ScheduleUpdatedEvent {
  schedule_id: string;
}

export interface ChatMessageEvent {
  conversation_id: string;
  message_id: string;
}

export interface NotificationCreatedEvent {
  kind: string;
  severity: "info" | "success" | "warning" | "error";
}

export interface HeartbeatEvent {
  time: IsoDateTime;
}

export type LiveEvent =
  | { type: "task.updated"; data: TaskUpdatedEvent }
  | { type: "approval.requested"; data: ApprovalRequestedEvent }
  | { type: "device.updated"; data: DeviceUpdatedEvent }
  | { type: "schedule.updated"; data: ScheduleUpdatedEvent }
  | { type: "chat.message"; data: ChatMessageEvent }
  | { type: "notification.created"; data: NotificationCreatedEvent }
  | { type: "heartbeat"; data: HeartbeatEvent };
