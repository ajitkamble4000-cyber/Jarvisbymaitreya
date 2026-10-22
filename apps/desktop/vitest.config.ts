// CommonJS form: this package builds to CommonJS, so the config is written the same way to avoid a loader warning.
const { defineConfig } = require("vitest/config") as typeof import("vitest/config");

module.exports = defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
    testTimeout: 20_000,
    hookTimeout: 20_000,
    pool: "forks",
  },
});
