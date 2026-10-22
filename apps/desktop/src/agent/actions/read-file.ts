import { cancelled, succeeded, type ActionInput, type ActionOutcome } from "./types.js";

export const MAX_READ_BYTES = 64 * 1024;

export async function readFile({ target, platform, signal }: ActionInput<"read_file">): Promise<ActionOutcome> {
  const { file } = target;
  if (signal.aborted) {
    return cancelled();
  }
  const result = await platform.readFileLimited(file.path, MAX_READ_BYTES);
  if (signal.aborted) {
    return cancelled();
  }
  return succeeded({
    file_id: file.id,
    content: result.content,
    truncated: result.truncated,
    size_bytes: result.sizeBytes,
  });
}
