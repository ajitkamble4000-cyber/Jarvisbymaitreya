/**
 * Wire protocol between the backend and the Windows agent (docs/desktop-protocol.md).
 *
 * Every inbound frame is validated here before any field is used. Outbound frames are built with
 * the same schemas so that tests can assert the exact shapes the backend expects.
 */
import { z } from "zod";

export const PROTOCOL_VERSION = 1;
export const CANCEL_ACTION = "__cancel__";

/** Actions the backend may request. Each action has a capability of the same name in agent.json. */
export const SUPPORTED_ACTIONS = [
  "launch_app",
  "open_url",
  "open_folder",
  "open_project",
  "system_info",
  "read_file",
  "app_availability",
  "run_check",
] as const;

export type ActionName = (typeof SUPPORTED_ACTIONS)[number];

export function isSupportedAction(value: string): value is ActionName {
  return (SUPPORTED_ACTIONS as readonly string[]).includes(value);
}

/** Error codes the agent may report in a failed result (section 4 and section 5 of the protocol). */
export const AGENT_ERROR_CODES = [
  "invalid_arguments",
  "permission_denied",
  "expired",
  "replayed",
  "unsupported_action",
  "signature_invalid",
  "resource_not_approved",
  "not_installed",
  "execution_failed",
  "cancelled_by_user",
  "agent_error",
  "check_failed",
  "timeout",
] as const;

export type AgentErrorCode = (typeof AGENT_ERROR_CODES)[number];

/** Resource ids match the backend rule in app/schemas/devices.py. */
export const RESOURCE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const resourceId = z.string().regex(RESOURCE_ID_PATTERN);
const timestamp = z.string().min(1).max(64);

// ---- server -> agent --------------------------------------------------------------------

export const HelloAckSchema = z.object({
  type: z.literal("hello_ack"),
  device_id: z.string().min(1).max(64),
  server_time: timestamp,
  heartbeat_interval_seconds: z.number().int().min(1).max(3600),
  protocol_version: z.number().int(),
});

export const CommandSchema = z.object({
  type: z.literal("command"),
  id: z.string().min(1).max(128),
  action: z.string().min(1).max(64),
  args_json: z.string().max(8192),
  issued_at: timestamp,
  expires_at: timestamp,
  nonce: z.string().min(1).max(256),
  signature: z.string().min(1).max(256),
  protocol_version: z.number().int(),
});

export const CancelSchema = z.object({
  type: z.literal("cancel"),
  command_id: z.string().min(1).max(128),
  issued_at: timestamp,
  expires_at: timestamp,
  nonce: z.string().min(1).max(256),
  signature: z.string().min(1).max(256),
});

export const PingSchema = z.object({
  type: z.literal("ping"),
  nonce: z.string().min(1).max(256),
  sent_at: z.string().max(64).optional(),
});

export const ServerErrorSchema = z.object({
  type: z.literal("error"),
  code: z.string().max(64),
  message: z.string().max(500),
});

export type HelloAck = z.infer<typeof HelloAckSchema>;
export type CommandMessage = z.infer<typeof CommandSchema>;
export type CancelMessage = z.infer<typeof CancelSchema>;
export type PingMessage = z.infer<typeof PingSchema>;
export type ServerError = z.infer<typeof ServerErrorSchema>;

export type ServerFrame =
  | { kind: "hello_ack"; message: HelloAck }
  | { kind: "command"; message: CommandMessage }
  | { kind: "cancel"; message: CancelMessage }
  | { kind: "ping"; message: PingMessage }
  | { kind: "error"; message: ServerError }
  | { kind: "unknown"; type: string }
  | { kind: "invalid"; reason: string };

const SERVER_SCHEMAS: Record<string, z.ZodType> = {
  hello_ack: HelloAckSchema,
  command: CommandSchema,
  cancel: CancelSchema,
  ping: PingSchema,
  error: ServerErrorSchema,
};

/**
 * Parse one inbound text frame. Unknown types are reported as "unknown" so the caller can log and
 * ignore them, as the protocol requires. Malformed frames are reported as "invalid".
 */
export function parseServerFrame(text: string): ServerFrame {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { kind: "invalid", reason: "not_json" };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { kind: "invalid", reason: "not_object" };
  }
  const type = (value as { type?: unknown }).type;
  if (typeof type !== "string") {
    return { kind: "invalid", reason: "missing_type" };
  }
  const schema = SERVER_SCHEMAS[type];
  if (schema === undefined) {
    return { kind: "unknown", type: type.slice(0, 40) };
  }
  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    return { kind: "invalid", reason: `schema_${type}` };
  }
  switch (type) {
    case "hello_ack":
      return { kind: "hello_ack", message: parsed.data as HelloAck };
    case "command":
      return { kind: "command", message: parsed.data as CommandMessage };
    case "cancel":
      return { kind: "cancel", message: parsed.data as CancelMessage };
    case "ping":
      return { kind: "ping", message: parsed.data as PingMessage };
    default:
      return { kind: "error", message: parsed.data as ServerError };
  }
}

// ---- action arguments (strict: unknown keys are rejected) ---------------------------------

export const ActionArgSchemas = {
  launch_app: z.object({ app_id: resourceId }).strict(),
  open_url: z.union([
    z.object({ url_id: resourceId }).strict(),
    z.object({ url: z.string().min(1).max(2048) }).strict(),
  ]),
  open_folder: z.object({ folder_id: resourceId }).strict(),
  open_project: z.object({ project_id: resourceId, app_id: resourceId.optional() }).strict(),
  system_info: z.object({}).strict(),
  read_file: z.object({ file_id: resourceId }).strict(),
  app_availability: z.object({ app_id: resourceId }).strict(),
  run_check: z.object({ check_id: resourceId }).strict(),
} satisfies Record<ActionName, z.ZodType>;

export type ActionArgs = {
  [A in ActionName]: z.infer<(typeof ActionArgSchemas)[A]>;
};

// ---- agent -> server --------------------------------------------------------------------

export const ResourceEntrySchema = z.object({
  id: resourceId,
  label: z.string().min(1).max(120),
});
export const UrlResourceEntrySchema = ResourceEntrySchema.extend({
  url: z.string().min(1).max(2048),
});

export const ResourcesSchema = z.object({
  apps: z.array(ResourceEntrySchema).max(50),
  urls: z.array(UrlResourceEntrySchema).max(50),
  folders: z.array(ResourceEntrySchema).max(50),
  projects: z.array(ResourceEntrySchema).max(50),
  files: z.array(ResourceEntrySchema).max(50),
  checks: z.array(ResourceEntrySchema).max(50),
});
export type Resources = z.infer<typeof ResourcesSchema>;

export const HelloSchema = z.object({
  type: z.literal("hello"),
  agent_version: z.string().min(1).max(40),
  os: z.string().min(1).max(30),
  os_version: z.string().max(120).nullable(),
  capabilities: z.array(z.string()).max(64),
  resources: ResourcesSchema,
});

export const HeartbeatSchema = z.object({
  type: z.literal("heartbeat"),
  running_command_ids: z.array(z.string()).max(32),
  sent_at: timestamp,
});

export const ProgressSchema = z.object({
  type: z.literal("progress"),
  command_id: z.string().min(1).max(128),
  step: z.string().min(1).max(200),
  message: z.string().max(500),
  progress: z.number().min(0).max(1).nullable(),
  sent_at: timestamp,
});

export const ErrorPayloadSchema = z.object({
  code: z.string().min(1).max(64),
  message: z.string().min(1).max(500),
  details: z.record(z.string(), z.unknown()),
});

export const ResultSchema = z.object({
  type: z.literal("result"),
  command_id: z.string().min(1).max(128),
  status: z.enum(["succeeded", "failed", "cancelled"]),
  output: z.record(z.string(), z.unknown()),
  error: ErrorPayloadSchema.nullable(),
  started_at: timestamp.nullable(),
  finished_at: timestamp,
});

export const PongSchema = z.object({
  type: z.literal("pong"),
  nonce: z.string().min(1).max(256),
});

export type Hello = z.infer<typeof HelloSchema>;
export type Heartbeat = z.infer<typeof HeartbeatSchema>;
export type Progress = z.infer<typeof ProgressSchema>;
export type ErrorPayload = z.infer<typeof ErrorPayloadSchema>;
export type AgentResult = z.infer<typeof ResultSchema>;
export type Pong = z.infer<typeof PongSchema>;

// ---- pairing (POST /api/v1/agent/pair) ----------------------------------------------------

export const PairResponseSchema = z.object({
  device_id: z.string().min(1).max(64),
  device_secret: z.string().min(16).max(512),
  websocket_path: z.string().regex(/^\/[A-Za-z0-9/_-]{1,200}$/),
  heartbeat_interval_seconds: z.number().int().min(1).max(3600),
  protocol_version: z.number().int(),
});
export type PairResponse = z.infer<typeof PairResponseSchema>;

export const PairRequestSchema = z.object({
  code: z.string().min(1).max(32),
  device_name: z.string().min(1).max(120),
  device_type: z.string().min(2).max(30),
  os: z.string().min(1).max(30),
  os_version: z.string().max(120).nullable(),
  agent_version: z.string().min(1).max(40),
  capabilities: z.array(z.string()).max(64),
  resources: ResourcesSchema,
});
export type PairRequest = z.infer<typeof PairRequestSchema>;
