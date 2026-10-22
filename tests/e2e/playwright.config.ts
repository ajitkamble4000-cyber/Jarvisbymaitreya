import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";
import path from "node:path";

// Milestone end-to-end configuration. Everything runs locally:
//   fake Gemini on 8788, backend on 8000 (fresh database jarvis_e2e), web preview on 5173.
// Chromium comes from /opt/pw-browsers (no browser download). Run from apps/web:
//   npx playwright test --config ../../tests/e2e/playwright.config.ts
const REPO = path.resolve(__dirname, "../..");
const WEB = path.join(REPO, "apps/web");
const PYTHON = path.join(REPO, "services/api/.venv/bin/python");
const E2E_DATABASE_URL = "postgresql+asyncpg://jarvis:jarvis@127.0.0.1:5432/jarvis_e2e";
// CHROMIUM_PATH wins. Otherwise use the sandbox Chromium when it exists, else Playwright's bundled browser.
const SANDBOX_CHROMIUM = "/opt/pw-browsers/chromium";
const CHROMIUM = process.env.CHROMIUM_PATH ?? (existsSync(SANDBOX_CHROMIUM) ? SANDBOX_CHROMIUM : undefined);

export default defineConfig({
  testDir: __dirname,
  testMatch: /.*\.spec\.ts/,
  outputDir: path.join(__dirname, ".artifacts", "test-results"),
  reporter: [["list"]],
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 240_000,
  globalTeardown: path.join(__dirname, "fixtures", "global-teardown.ts"),
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    trace: "off",
    screenshot: "only-on-failure",
    launchOptions: { executablePath: CHROMIUM },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: [
    {
      command: `node ${path.join(__dirname, "fixtures", "fake-gemini.ts")}`,
      url: "http://127.0.0.1:8788/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `sh -c '"${PYTHON}" "${path.join(__dirname, "fixtures", "create-e2e-database.py")}" && sh "${path.join(REPO, "scripts", "dev-backend.sh")}"'`,
      url: "http://127.0.0.1:8000/api/v1/health/live",
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        DATABASE_URL: E2E_DATABASE_URL,
        GEMINI_BASE_URL: "http://127.0.0.1:8788",
        DATA_DIR: path.join(__dirname, ".artifacts", "backend-data"),
      },
    },
    {
      command: "npx vite preview --host 127.0.0.1 --port 5173 --strictPort",
      cwd: WEB,
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
