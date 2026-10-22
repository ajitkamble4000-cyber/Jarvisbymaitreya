/**
 * First milestone in a real browser: set up the owner, store a Gemini key, test it, pair a desktop agent,
 * ask for "Open Chrome on my laptop", approve the proposal, and see the real result on the dashboard.
 *
 * The web app is served by `vite preview`, which proxies /api to the backend. The backend runs on a fresh
 * database (jarvis_e2e) with GEMINI_BASE_URL pointed at tests/e2e/fixtures/fake-gemini.ts. The desktop
 * agent is the headless CLI in simulated mode, started by this test with the code shown in the dialog.
 * Set E2E_SCREENSHOT_DIR to choose where the dashboard screenshots are written.
 */
import { test, expect, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const REPO = path.resolve(__dirname, "../..");
const AGENT_CLI = path.join(REPO, "apps/desktop/dist/cli/headless.js");
const ARTIFACTS = path.join(__dirname, ".artifacts");
const SCREENSHOT_DIR = process.env.E2E_SCREENSHOT_DIR ?? ARTIFACTS;
const OWNER = { email: "owner@example.com", password: "milestone-owner-pass-2026", name: "Milestone owner" };
const FAKE_KEY = "AIza-fake-e2e-key-0001";
const MODEL = "gemini-2.5-flash";
const PROMPT = "Open Chrome on my laptop.";
// The model's proposal names the device "Laptop", so the simulated agent registers under that name.
const DEVICE_NAME = "Laptop";
const TASK_TITLE = "Launch Google Chrome on Laptop";

async function startAgent(code: string, dataDir: string): Promise<ChildProcess> {
  // A fresh agent folder per run: a stale secret from an earlier database would not match.
  fs.rmSync(dataDir, { recursive: true, force: true });
  fs.mkdirSync(dataDir, { recursive: true });
  const log = fs.createWriteStream(path.join(ARTIFACTS, "agent.log"), { flags: "w" });
  const child = spawn(
    process.execPath,
    [
      AGENT_CLI,
      "--server",
      "http://127.0.0.1:8000",
      "--code",
      code,
      "--platform",
      "simulated",
      "--device-name",
      DEVICE_NAME,
      "--data-dir",
      dataDir,
    ],
    { cwd: path.join(REPO, "apps/desktop"), stdio: ["ignore", "pipe", "pipe"] },
  );
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  return child;
}

async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "page must not scroll sideways").toBeLessThanOrEqual(0);
}

test("first milestone: owner, Gemini key, pairing, approval, real result", async ({ page }) => {
  test.setTimeout(240_000);
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  let agent: ChildProcess | undefined;

  try {
    await test.step("set up the owner account", async () => {
      await page.goto("/setup");
      await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
      await page.getByLabel("Display name (optional)").fill(OWNER.name);
      await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
      await page.getByLabel("Confirm password").fill(OWNER.password);
      await page.getByRole("button", { name: "Create owner account" }).click();
      await page.waitForURL(/\/overview/, { timeout: 30_000 });
    });

    await test.step("store the Gemini key, test it, select a model", async () => {
      await page.goto("/models");
      const keyField = page.getByLabel("API key").first();
      await keyField.fill(FAKE_KEY);
      await page.getByRole("button", { name: "Save key" }).first().click();
      await expect(keyField).toHaveValue("", { timeout: 15_000 });
      await expect(page.getByText("0001").first()).toBeVisible({ timeout: 15_000 });
      await page.getByRole("button", { name: "Test connection" }).first().click();
      await expect(page.getByText("Google Gemini accepted the API key.").first()).toBeVisible({ timeout: 20_000 });
      await page.getByLabel("Selected model").first().selectOption(MODEL);
      await expect(page.getByLabel("Selected model").first()).toHaveValue(MODEL);
      await expect(page.locator("body")).not.toContainText(FAKE_KEY);
    });

    let code = "";
    await test.step("create a pairing code and start the desktop agent with it", async () => {
      await page.goto("/devices?pair=1");
      await page.getByRole("button", { name: "Create pairing code" }).click();
      const label = page.locator('[aria-label^="Pairing code "]');
      await expect(label).toBeVisible({ timeout: 15_000 });
      code = ((await label.getAttribute("aria-label")) ?? "").replace("Pairing code ", "").replace(/ /g, "");
      expect(code).toMatch(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/);
      agent = await startAgent(code, path.join(ARTIFACTS, "agent-data"));
      await page.keyboard.press("Escape");
      await expect(page.getByRole("status").filter({ hasText: "1 paired device" })).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText("Online").first()).toBeVisible({ timeout: 30_000 });
    });

    await test.step("ask in chat, see the approval card, approve", async () => {
      await page.goto("/chat");
      await page.locator("#chat-composer").fill(PROMPT);
      await page.getByRole("button", { name: "Send", exact: true }).click();
      const approve = page.getByRole("button", { name: "Approve" });
      await expect(approve).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText(TASK_TITLE).first()).toBeVisible();
      await approve.click();
    });

    await test.step("the real result reaches the chat and the dashboard", async () => {
      await expect(page.getByText(/SIMULATED: Started Google Chrome \(pid \d+\)/).first()).toBeVisible({ timeout: 60_000 });

      await page.goto("/overview");
      const row = page.locator("li").filter({ hasText: TASK_TITLE }).first();
      await expect(row).toContainText(/Completed/i, { timeout: 30_000 });

      await page.setViewportSize({ width: 1280, height: 900 });
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "dashboard-1280.png"), fullPage: true });
      await expectNoHorizontalScroll(page);

      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "dashboard-390.png"), fullPage: true });
      await expectNoHorizontalScroll(page);
    });
  } finally {
    agent?.kill();
  }
});
