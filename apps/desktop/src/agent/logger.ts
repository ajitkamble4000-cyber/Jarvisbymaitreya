/**
 * Logging for the agent. Fields are filtered so that secrets, signatures, nonces and pairing codes never reach a sink,
 * even if a caller passes them by mistake.
 */

export interface Logger {
  debug(message: string, fields?: LogFields): void;
  info(message: string, fields?: LogFields): void;
  warn(message: string, fields?: LogFields): void;
  error(message: string, fields?: LogFields): void;
}

export type LogFields = Record<string, string | number | boolean | null>;

// Error codes are not secret and are logged. The pairing code is never passed to a logger at all.
const SENSITIVE_KEY = /secret|token|signature|nonce|password|pairing|args_json/i;

export function redactFields(fields: LogFields | undefined): LogFields {
  if (fields === undefined) {
    return {};
  }
  const safe: LogFields = {};
  for (const [key, value] of Object.entries(fields)) {
    if (!SENSITIVE_KEY.test(key)) {
      safe[key] = value;
    }
  }
  return safe;
}

export type LogSink = (level: "debug" | "info" | "warn" | "error", line: string) => void;

/** Builds a Logger that writes one line per call through the sink. */
export function createLogger(sink: LogSink, scope = "jarvis.agent"): Logger {
  const write = (level: "debug" | "info" | "warn" | "error", message: string, fields?: LogFields): void => {
    const safe = redactFields(fields);
    const suffix = Object.keys(safe).length === 0 ? "" : ` ${JSON.stringify(safe)}`;
    sink(level, `${new Date().toISOString()} ${level.toUpperCase()} ${scope} ${message}${suffix}`);
  };
  return {
    debug: (message, fields) => write("debug", message, fields),
    info: (message, fields) => write("info", message, fields),
    warn: (message, fields) => write("warn", message, fields),
    error: (message, fields) => write("error", message, fields),
  };
}

export const silentLogger: Logger = {
  debug: () => undefined,
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};
