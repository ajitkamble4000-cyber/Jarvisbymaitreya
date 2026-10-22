/**
 * The quick window in a real browser: set up the owner, store a Gemini key, pair a desktop agent, then use the
 * /quick page at the desktop popup's size. A plain request gets a reply. "Open Chrome on my laptop" gets an approval
 * card inside the popup, and after Approve the task completes on the simulated agent.
 *
 * Run it alone, like the milestone: it creates the owner on a fresh database.
 *   cd apps/web && npx playwright test quick.spec.ts --config ../../tests/e2e/playwright.config.ts
 */
import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const REPO = path.resolve(__dirname, "../..");
const AGENT_CLI = path.join(REPO, "apps/desktop/dist/cli/headless.js");
const ARTIFACTS = path.join(__dirname, ".artifacts");
const OWNER = { email: "quick-owner@example.com", password: "quick-owner-pass-2026", name: "Quick owner" };
const FAKE_KEY = "AIza-fake-e2e-key-0001";
const MODEL = "gemini-2.5-flash";
const DEVICE_NAME = "Laptop";
const TASK_TITLE = "Launch Google Chrome on Laptop";
// The desktop popup's size (apps/desktop/src/shared/quick.ts: QUICK_WINDOW_SIZE).
const POPUP = { width: 640, height: 430 };

/** A stand-in for the browser's speech engine. It delivers the text that the test sends with window.__say(). */
const FAKE_SPEECH_ENGINE = `
  (() => {
    class FakeSpeechRecognition {
      constructor() {
        this.lang = ""; this.continuous = false; this.interimResults = false; this.maxAlternatives = 1;
        this.onstart = null; this.onend = null; this.onerror = null; this.onresult = null; this.onspeechstart = null;
        window.__speech = this;
      }
      start() { setTimeout(() => this.onstart && this.onstart(), 0); }
      stop() { setTimeout(() => this.onend && this.onend(), 0); }
      abort() { setTimeout(() => this.onend && this.onend(), 0); }
    }
    window.SpeechRecognition = FakeSpeechRecognition;
    window.webkitSpeechRecognition = FakeSpeechRecognition;
    window.__say = (text) => {
      const results = [Object.assign([{ transcript: text, confidence: 0.9 }], { isFinal: true, length: 1 })];
      window.__speech.onresult({ resultIndex: 0, results });
    };
  })();
`;

async function startAgent(code: string, dataDir: string): Promise<ChildProcess> {
  fs.rmSync(dataDir, { recursive: true, force: true });
  fs.mkdirSync(dataDir, { recursive: true });
  const log = fs.createWriteStream(path.join(ARTIFACTS, "quick-agent.log"), { flags: "w" });
  const child = spawn(
    process.execPath,
    [AGENT_CLI, "--server", "http://127.0.0.1:8000", "--code", code, "--platform", "simulated", "--device-name", DEVICE_NAME, "--data-dir", dataDir],
    { cwd: path.join(REPO, "apps/desktop"), stdio: ["ignore", "pipe", "pipe"] },
  );
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  return child;
}

test("quick window: a reply, then an approved action from the popup", async ({ page }) => {
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

    await test.step("store the Gemini key and choose the model", async () => {
      await page.goto("/models");
      const keyField = page.getByLabel("API key").first();
      await keyField.fill(FAKE_KEY);
      await page.getByRole("button", { name: "Save key" }).first().click();
      await expect(keyField).toHaveValue("", { timeout: 15_000 });
      await page.getByRole("button", { name: "Test connection" }).first().click();
      await expect(page.getByText("Google Gemini accepted the API key.").first()).toBeVisible({ timeout: 20_000 });
      await page.getByLabel("Selected model").first().selectOption(MODEL);
      await expect(page.getByLabel("Selected model").first()).toHaveValue(MODEL);
    });

    await test.step("pair the simulated desktop agent", async () => {
      await page.goto("/devices?pair=1");
      await page.getByRole("button", { name: "Create pairing code" }).click();
      const label = page.locator('[aria-label^="Pairing code "]');
      await expect(label).toBeVisible({ timeout: 15_000 });
      const code = ((await label.getAttribute("aria-label")) ?? "").replace("Pairing code ", "").replace(/ /g, "");
      agent = await startAgent(code, path.join(ARTIFACTS, "quick-agent-data"));
      await page.keyboard.press("Escape");
      await expect(page.getByText("Online").first()).toBeVisible({ timeout: 60_000 });
    });

    await test.step("a plain request in the popup gets a reply", async () => {
      await page.setViewportSize(POPUP);
      await page.goto("/quick");
      await page.getByLabel("What should JARVIS do?").fill("Say hello");
      await page.getByLabel("What should JARVIS do?").press("Enter");
      await expect(page.getByText("Hello.", { exact: true })).toBeVisible({ timeout: 60_000 });
      await expect(page.getByRole("link", { name: "Open in chat" })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, "the popup must not scroll sideways").toBeLessThanOrEqual(0);
    });

    await test.step("a device action is approved inside the popup", async () => {
      await page.goto("/quick");
      await page.getByLabel("What should JARVIS do?").fill("Open Chrome on my laptop.");
      await page.getByLabel("What should JARVIS do?").press("Enter");
      const approve = page.getByRole("button", { name: "Approve" });
      await expect(approve).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText(TASK_TITLE).first()).toBeVisible();
      await page.screenshot({ path: path.join(ARTIFACTS, "quick-popup-approval.png") });
      await approve.click();
    });

    await test.step("the result shows on the dashboard", async () => {
      await page.goto("/overview");
      const row = page.locator("li").filter({ hasText: TASK_TITLE }).first();
      await expect(row).toContainText(/Completed/i, { timeout: 60_000 });
    });

    await test.step("Hey JARVIS on another page opens the quick window with the request", async () => {
      await page.goto("/settings?tab=voice");
      const wakeSwitch = page.getByRole("switch", { name: "Hey JARVIS" });
      await expect(wakeSwitch).toHaveAttribute("aria-checked", "false");
      await wakeSwitch.click();
      await expect(wakeSwitch).toHaveAttribute("aria-checked", "true", { timeout: 15_000 });

      await page.addInitScript(FAKE_SPEECH_ENGINE);
      await page.goto("/overview");
      await expect(page.getByRole("status").filter({ hasText: "Listening for" })).toBeVisible({ timeout: 15_000 });
      await page.waitForFunction(() => typeof (window as unknown as { __say?: unknown }).__say === "function");
      await page.evaluate(() => (window as unknown as { __say: (text: string) => void }).__say("Hey JARVIS, say hello"));

      await expect(page).toHaveURL(/\/quick/, { timeout: 15_000 });
      await expect(page.getByText("Hello.", { exact: true })).toBeVisible({ timeout: 60_000 });
    });
  } finally {
    agent?.kill();
  }
});
