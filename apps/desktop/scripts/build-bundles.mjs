// Bundles the sandboxed preload script and the renderer, and copies the renderer's static files into dist/.
// The sandboxed preload cannot require local modules, so it must be one file. The renderer is a classic script
// (an IIFE), so that it runs under the strict Content-Security-Policy with script-src 'self' and no module loading.
import { copyFile, mkdir } from "node:fs/promises";
import { build } from "esbuild";

await mkdir("dist/preload", { recursive: true });
await mkdir("dist/renderer", { recursive: true });

await build({
  entryPoints: ["src/preload/index.ts"],
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  external: ["electron"],
  outfile: "dist/preload/index.js",
  logLevel: "warning",
});

// The quick-ask popup has its own preload, with the single hide() call. Built the same way.
await build({
  entryPoints: ["src/preload/quick.ts"],
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  external: ["electron"],
  outfile: "dist/preload/quick.js",
  logLevel: "warning",
});

await build({
  entryPoints: ["src/renderer/main.ts"],
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "es2022",
  outfile: "dist/renderer/main.js",
  logLevel: "warning",
});

await copyFile("src/renderer/index.html", "dist/renderer/index.html");
await copyFile("src/renderer/styles.css", "dist/renderer/styles.css");
console.log("Bundles written to dist/preload and dist/renderer.");
