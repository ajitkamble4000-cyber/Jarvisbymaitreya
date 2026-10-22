import { cancelled, succeeded, type ActionInput, type ActionOutcome } from "./types.js";

export async function systemInfo({ platform, signal }: ActionInput<"system_info">): Promise<ActionOutcome> {
  if (signal.aborted) {
    return cancelled();
  }
  const info = platform.systemInfo();
  return succeeded({
    hostname: info.hostname,
    os: info.os,
    os_version: info.os_version,
    arch: info.arch,
    cpu_count: info.cpu_count,
    total_memory_mb: info.total_memory_mb,
    free_memory_mb: info.free_memory_mb,
    uptime_seconds: info.uptime_seconds,
  });
}
