/**
 * Accessibility checks with axe-core (WCAG 2.0 and 2.1, levels A and AA) on every main page of the web app,
 * at desktop width and at a 390 px phone width for /overview and /chat. Any violation with impact critical or
 * serious fails the test. Every violation found, of any impact, is written to
 * tests/e2e/.artifacts/a11y-report.json, sorted so that the same run gives the same file.
 *
 * Not covered by axe: screen reader output, keyboard-only flows and focus order, whether alt text is meaningful,
 * states that these pages do not show in this run (open dialogs, error states, lists with real data), and
 * contrast of text that is still fading in. Reduced motion is emulated so that entrance animations are finished.
 */
import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import type { Result } from "axe-core";

const ARTIFACTS = path.join(__dirname, ".artifacts");
const REPORT_PATH = path.join(ARTIFACTS, "a11y-report.json");
const OWNER = { email: "a11y-owner@example.com", password: "a11y-owner-pass-2026", name: "Accessibility owner" };
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const BLOCKING_IMPACTS = ["critical", "serious"];
const IMPACT_ORDER = ["critical", "serious", "moderate", "minor", "none"];
const DESKTOP = { width: 1280, height: 900 };
const PHONE = { width: 390, height: 844 };
const DESKTOP_ROUTES = [
  "/overview",
  "/chat",
  "/voice",
  "/models",
  "/devices",
  "/tasks",
  "/schedule",
  "/memory",
  "/notifications",
  "/explore",
  "/integrations",
  "/analytics",
  "/activity",
  "/developer",
  "/settings",
  "/settings?tab=users",
  "/automation",
  "/files",
  "/help",
  "/quick",
];
const PHONE_ROUTES = ["/overview", "/chat", "/quick"];

interface Violation {
  impact: string;
  rule: string;
  count: number;
  firstTarget: string;
}

interface RouteReport {
  route: string;
  viewport: string;
  rendered: boolean;
  rulesPassed: number;
  rulesNeedingReview: number;
  violations: Violation[];
}

async function createOwner(page: Page): Promise<void> {
  await page.goto("/setup");
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Display name (optional)").fill(OWNER.name);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByLabel("Confirm password").fill(OWNER.password);
  await page.getByRole("button", { name: "Create owner account" }).click();
  await page.waitForURL(/\/overview/, { timeout: 30_000 });
}

function firstTarget(result: Result): string {
  const first = result.nodes[0]?.target[0];
  if (first === undefined) return "";
  return typeof first === "string" ? first : JSON.stringify(first);
}

async function scan(page: Page, route: string, viewport: string): Promise<RouteReport> {
  await page.goto(route);
  const rendered = await page
    .locator("#main-content")
    .waitFor({ state: "visible", timeout: 20_000 })
    .then(() => true, () => false);
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const violations = results.violations
    .map((result) => ({
      impact: result.impact ?? "none",
      rule: result.id,
      count: result.nodes.length,
      firstTarget: firstTarget(result),
    }))
    .sort(
      (a, b) =>
        IMPACT_ORDER.indexOf(a.impact) - IMPACT_ORDER.indexOf(b.impact) ||
        a.rule.localeCompare(b.rule) ||
        a.firstTarget.localeCompare(b.firstTarget),
    );
  return {
    route,
    viewport,
    rendered,
    rulesPassed: results.passes.length,
    rulesNeedingReview: results.incomplete.length,
    violations,
  };
}

test("axe finds no critical or serious WCAG 2.x A/AA violations on any main page", async ({ page }) => {
  test.setTimeout(600_000);
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize(DESKTOP);
  await createOwner(page);

  const reports: RouteReport[] = [];
  for (const route of DESKTOP_ROUTES) {
    reports.push(await test.step(`desktop ${route}`, () => scan(page, route, "desktop 1280x900")));
  }
  await page.setViewportSize(PHONE);
  for (const route of PHONE_ROUTES) {
    reports.push(await test.step(`phone ${route}`, () => scan(page, route, "phone 390x844")));
  }

  fs.writeFileSync(
    REPORT_PATH,
    `${JSON.stringify(
      {
        tool: "axe-core 4.14.0 via @axe-core/playwright 4.14.0",
        tags: WCAG_TAGS,
        blockingImpacts: BLOCKING_IMPACTS,
        routes: reports,
      },
      null,
      2,
    )}\n`,
  );

  const unrendered = reports.filter((r) => !r.rendered).map((r) => `${r.viewport} ${r.route}`);
  const blocking = reports.flatMap((r) =>
    r.violations
      .filter((v) => BLOCKING_IMPACTS.includes(v.impact))
      .map((v) => `${r.viewport} ${r.route} ${v.rule} (${v.impact}) x${v.count} first target: ${v.firstTarget}`),
  );
  expect(unrendered, "every route must render before it is scanned").toEqual([]);
  expect(blocking, "critical or serious violations").toEqual([]);
});
