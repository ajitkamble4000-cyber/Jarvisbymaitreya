/**
 * Relative paths the backend may name inside an approved workspace (create_files). The workspace folder itself
 * always comes from agent.json; the server only names the folders and files to create beneath it.
 *
 * A safe path uses forward slashes, has 1 to 8 segments, and every segment is a plain file or folder name:
 * no "." or "..", no drive letters, no backslashes, no ".git", no Windows reserved names, and no trailing dot or space.
 */

export const MAX_RELATIVE_PATH_LENGTH = 240;
export const MAX_PATH_SEGMENTS = 8;
const SEGMENT = /^[A-Za-z0-9._-][A-Za-z0-9._ -]{0,99}$/;
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;

export function isSafeSegment(segment: string): boolean {
  if (!SEGMENT.test(segment)) {
    return false;
  }
  if (segment === "." || segment === "..") {
    return false;
  }
  if (segment.endsWith(".") || segment.endsWith(" ")) {
    return false;
  }
  if (segment.toLowerCase() === ".git") {
    return false;
  }
  return !RESERVED.test(segment);
}

export function isSafeRelativePath(value: string): boolean {
  if (value.length === 0 || value.length > MAX_RELATIVE_PATH_LENGTH) {
    return false;
  }
  const segments = value.split("/");
  if (segments.length > MAX_PATH_SEGMENTS) {
    return false;
  }
  return segments.every(isSafeSegment);
}

/** Case-insensitive key, because Windows treats "App.py" and "app.py" as the same file. */
export function pathKey(value: string): string {
  return value.toLowerCase();
}
