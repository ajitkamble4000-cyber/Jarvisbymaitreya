/**
 * File helpers for agent state: owner-only directories and files on POSIX, atomic replace on every platform.
 * On Windows the owner-only mode bits have no effect; the file lives in the user's %APPDATA% folder, which
 * Windows already restricts to the user. This limitation is stated in the README.
 */
import * as fs from "node:fs";
import * as path from "node:path";

const PRIVATE_FILE_MODE = 0o600;
const PRIVATE_DIR_MODE = 0o700;
const isPosix = process.platform !== "win32";

export async function ensurePrivateDir(dir: string): Promise<void> {
  await fs.promises.mkdir(dir, { recursive: true, mode: PRIVATE_DIR_MODE });
  if (isPosix) {
    await fs.promises.chmod(dir, PRIVATE_DIR_MODE);
  }
}

/** Writes the file through a temporary sibling and a rename, so a crash never leaves a half-written file. */
export async function writePrivateFileAtomic(file: string, contents: string): Promise<void> {
  const dir = path.dirname(file);
  await ensurePrivateDir(dir);
  const temp = `${file}.${process.pid}.${Date.now()}.tmp`;
  const handle = await fs.promises.open(temp, "w", PRIVATE_FILE_MODE);
  try {
    await handle.writeFile(contents, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  if (isPosix) {
    await fs.promises.chmod(temp, PRIVATE_FILE_MODE);
  }
  await fs.promises.rename(temp, file);
}

/** Returns the file text, or null when the file does not exist. Other errors propagate. */
export async function readTextIfExists(file: string): Promise<string | null> {
  try {
    return await fs.promises.readFile(file, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return null;
    }
    throw error;
  }
}
