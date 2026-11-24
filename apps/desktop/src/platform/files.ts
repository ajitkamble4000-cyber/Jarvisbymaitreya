/**
 * Creates new files inside an approved workspace folder (create_files). Shared by the Windows platform and the tests.
 *
 * Guarantees:
 * - The workspace root comes from agent.json. Every target is resolved and must stay inside it.
 * - Nothing is overwritten: if any target already exists, nothing is written at all. Each file is then opened with
 *   the exclusive "wx" flag, so a file that appears in the meantime is not replaced either.
 * - A folder on the way that is a link (symlink or junction) to somewhere else is refused.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { PlatformError } from "./platform.js";
import { isSafeRelativePath, pathKey } from "../shared/safe-paths.js";

export interface NewFile {
  /** Relative to the project folder, forward slashes. */
  path: string;
  content: string;
}

export interface WriteNewFilesRequest {
  /** Absolute workspace root from agent.json. Created if it does not exist yet. */
  root: string;
  /** Project folder relative to the root, forward slashes. */
  folder: string;
  files: readonly NewFile[];
}

export interface WriteNewFilesResult {
  folderPath: string;
  created: string[];
}

function inside(parent: string, child: string): boolean {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

async function exists(target: string): Promise<boolean> {
  try {
    await fs.promises.lstat(target);
    return true;
  } catch {
    return false;
  }
}

export async function writeNewFiles(request: WriteNewFilesRequest): Promise<WriteNewFilesResult> {
  if (!path.isAbsolute(request.root)) {
    throw new PlatformError("execution_failed", "The workspace folder is not an absolute path.");
  }
  if (!isSafeRelativePath(request.folder)) {
    throw new PlatformError("permission_denied", "The project folder name is not allowed.");
  }
  await fs.promises.mkdir(request.root, { recursive: true });
  const rootStats = await fs.promises.lstat(request.root);
  if (rootStats.isSymbolicLink() || !rootStats.isDirectory()) {
    throw new PlatformError("permission_denied", "The workspace folder is not a plain folder.");
  }
  const realRoot = await fs.promises.realpath(request.root);
  const folderPath = path.resolve(realRoot, ...request.folder.split("/"));
  if (!inside(realRoot, folderPath) || folderPath === realRoot) {
    throw new PlatformError("permission_denied", "The project folder must be inside the workspace.");
  }

  const seen = new Set<string>();
  const targets: Array<{ file: NewFile; target: string }> = [];
  for (const file of request.files) {
    if (!isSafeRelativePath(file.path)) {
      throw new PlatformError("permission_denied", "A file name is not allowed.");
    }
    const key = pathKey(file.path);
    if (seen.has(key)) {
      throw new PlatformError("execution_failed", "The same file is listed twice.");
    }
    seen.add(key);
    const target = path.resolve(folderPath, ...file.path.split("/"));
    if (!inside(folderPath, target) || target === folderPath) {
      throw new PlatformError("permission_denied", "A file must be inside the project folder.");
    }
    targets.push({ file, target });
  }

  // Nothing is written unless every target is free.
  for (const { target } of targets) {
    if (await exists(target)) {
      throw new PlatformError("execution_failed", "A file with that name already exists. Nothing was changed.");
    }
  }

  // Walk down from the root, creating each missing folder and refusing any existing folder that is a link.
  const ensureFolder = async (folder: string): Promise<void> => {
    const relative = path.relative(realRoot, folder);
    let current = realRoot;
    for (const segment of relative.split(path.sep).filter((part) => part.length > 0)) {
      current = path.join(current, segment);
      let stats: fs.Stats | null = null;
      try {
        stats = await fs.promises.lstat(current);
      } catch {
        stats = null;
      }
      if (stats === null) {
        await fs.promises.mkdir(current);
        continue;
      }
      if (stats.isSymbolicLink() || !stats.isDirectory()) {
        throw new PlatformError("permission_denied", "A folder in the project path is a link or a file.");
      }
      const real = await fs.promises.realpath(current);
      if (path.resolve(real).toLowerCase() !== path.resolve(current).toLowerCase()) {
        throw new PlatformError("permission_denied", "A folder in the project path is a link.");
      }
    }
  };

  await ensureFolder(folderPath);
  const created: string[] = [];
  for (const { file, target } of targets) {
    await ensureFolder(path.dirname(target));
    try {
      await fs.promises.writeFile(target, file.content, { encoding: "utf8", flag: "wx" });
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      throw new PlatformError(
        "execution_failed",
        code === "EEXIST"
          ? `A file appeared while writing. ${created.length} of ${targets.length} files were created.`
          : `A file could not be written. ${created.length} of ${targets.length} files were created.`,
      );
    }
    created.push(file.path);
  }
  return { folderPath, created };
}
