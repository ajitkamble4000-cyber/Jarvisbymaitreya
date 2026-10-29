import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";

/**
 * The web app root. `npm test` runs in apps/web, so the working directory is that folder. From the
 * repository root it is apps/web beneath it. The jsdom environment has no file URL for this module.
 */
const APP_ROOT = existsSync(resolve(process.cwd(), "index.html")) ? resolve(process.cwd()) : resolve(process.cwd(), "apps", "web");
const PUBLIC = join(APP_ROOT, "public");

function readText(path: string): string {
  return readFileSync(path, "utf8");
}

/** Width, height and the colour of the top-left pixel, read from a PNG that gen-icons.py wrote (8-bit RGB, filter 0). */
function readPng(path: string): { width: number; height: number; corner: string } {
  const bytes = readFileSync(path);
  expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  const width = bytes.readUInt32BE(16);
  const height = bytes.readUInt32BE(20);
  expect(bytes[24]).toBe(8); // bit depth
  expect(bytes[25]).toBe(2); // colour type RGB
  const idat: Buffer[] = [];
  let offset = 8;
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    if (type === "IDAT") idat.push(bytes.subarray(offset + 8, offset + 8 + length));
    offset += 12 + length;
  }
  const raw = inflateSync(Buffer.concat(idat));
  // Row 0 starts with its filter byte, then the first pixel's red, green and blue.
  const corner = `#${raw[1].toString(16).padStart(2, "0")}${raw[2].toString(16).padStart(2, "0")}${raw[3].toString(16).padStart(2, "0")}`;
  return { width, height, corner };
}

/** The dark theme tokens from index.css, so that the manifest colours cannot drift from the design. */
function darkToken(name: string): string {
  const css = readText(join(APP_ROOT, "src", "styles", "index.css"));
  const block = /:root\[data-theme="dark"\]\s*\{([^}]*)\}/.exec(css);
  expect(block).not.toBeNull();
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block?.[1] ?? "");
  expect(match).not.toBeNull();
  return (match?.[1] ?? "").toLowerCase();
}

describe("PWA: manifest", () => {
  const manifest = JSON.parse(readText(join(PUBLIC, "manifest.webmanifest"))) as {
    name: string;
    short_name: string;
    start_url: string;
    display: string;
    background_color: string;
    theme_color: string;
    icons: Array<{ src: string; sizes: string; type: string; purpose?: string }>;
  };

  it("has the name, start page and standalone display the app needs", () => {
    expect(manifest.name).toBe("JARVIS AI");
    expect(manifest.short_name).toBe("JARVIS");
    expect(manifest.start_url).toBe("/chat");
    expect(manifest.display).toBe("standalone");
  });

  it("takes its colours from the dark theme tokens", () => {
    expect(manifest.background_color.toLowerCase()).toBe(darkToken("canvas"));
    expect(manifest.theme_color.toLowerCase()).toBe(darkToken("canvas"));
  });

  it("lists 192 and 512 pixel PNG icons that exist and have the stated size", () => {
    const sizes = manifest.icons.map((icon) => icon.sizes).sort();
    expect(sizes).toEqual(["192x192", "512x512"]);
    for (const icon of manifest.icons) {
      expect(icon.type).toBe("image/png");
      const [size] = icon.sizes.split("x");
      const file = join(PUBLIC, icon.src.replace(/^\//, ""));
      expect(existsSync(file)).toBe(true);
      const png = readPng(file);
      expect(png.width).toBe(Number(size));
      expect(png.height).toBe(Number(size));
    }
  });
});

describe("PWA: icons", () => {
  it("has a 180 pixel apple-touch-icon, filled with the dark accent colour", () => {
    const file = join(PUBLIC, "icons", "apple-touch-icon.png");
    expect(existsSync(file)).toBe(true);
    const png = readPng(file);
    expect(png.width).toBe(180);
    expect(png.height).toBe(180);
    expect(png.corner).toBe(darkToken("accent"));
  });

  it("the 192 and 512 icons use the same accent square", () => {
    for (const name of ["icon-192.png", "icon-512.png"]) {
      expect(readPng(join(PUBLIC, "icons", name)).corner).toBe(darkToken("accent"));
    }
  });
});

describe("PWA: index.html", () => {
  const html = readText(join(APP_ROOT, "index.html"));

  it("links the manifest, the apple touch icon and the theme colour", () => {
    expect(html).toMatch(/<link rel="manifest" href="\/manifest\.webmanifest"/);
    expect(html).toMatch(/<link rel="apple-touch-icon" sizes="180x180" href="\/icons\/apple-touch-icon\.png"/);
    expect(html).toMatch(/<meta name="theme-color" content="#111814"/);
  });

  it("declares the iOS web app settings", () => {
    expect(html).toMatch(/<meta name="apple-mobile-web-app-capable" content="yes"/);
    expect(html).toMatch(/<meta name="apple-mobile-web-app-title" content="JARVIS"/);
  });
});

describe("PWA: no service worker and no offline support", () => {
  it("does not register a service worker anywhere in the app", () => {
    const files: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir)) {
        if (entry === "node_modules" || entry === "dist") continue;
        const path = join(dir, entry);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.(ts|tsx|js|html)$/.test(entry) && entry !== "web-b-pwa.test.ts") files.push(path);
      }
    };
    walk(join(APP_ROOT, "src"));
    files.push(join(APP_ROOT, "index.html"), join(PUBLIC, "theme-init.js"));
    for (const file of files) {
      expect(readText(file), file).not.toMatch(/serviceWorker/);
    }
  });
});
