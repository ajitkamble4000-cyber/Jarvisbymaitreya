import { execFileSync } from "node:child_process";
import path from "node:path";

/** Drops the throw-away jarvis_e2e database after the run. The dev database is never touched. */
export default function globalTeardown(): void {
  const repo = path.resolve(__dirname, "../../..");
  const python = path.join(repo, "services/api/.venv/bin/python");
  const script = path.join(__dirname, "create-e2e-database.py");
  execFileSync(python, [script, "--drop"], { stdio: "inherit" });
}
