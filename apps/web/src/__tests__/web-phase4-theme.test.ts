import { describe, expect, it } from "vitest";
import { resolveDataTheme } from "../providers/ThemeProvider";
import { fromAppearanceSettings, sanitize } from "../stores/ui-store";
import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("high-contrast theme", () => {
  it("is applied as its own data-theme value, not as dark", () => {
    expect(resolveDataTheme("high-contrast")).toBe("high-contrast");
    expect(resolveDataTheme("system")).toBeNull();
  });

  it("is kept when a saved appearance is read back", () => {
    expect(sanitize({ theme: "high-contrast" })).toEqual({ theme: "high-contrast" });
    expect(fromAppearanceSettings({ theme: "high-contrast" } as never)).toMatchObject({ theme: "high-contrast" });
    expect(sanitize({ theme: "neon" })).toEqual({});
  });

  it("has its own token block in the stylesheet, with pure black and white", () => {
    const css = readFileSync(join(__dirname, "..", "styles", "index.css"), "utf8");
    const block = /:root\[data-theme="high-contrast"\]\s*\{([^}]*)\}/.exec(css);
    expect(block).not.toBeNull();
    expect(block?.[1]).toContain("--canvas: #000000");
    expect(block?.[1]).toContain("--ink: #ffffff");
  });
});
