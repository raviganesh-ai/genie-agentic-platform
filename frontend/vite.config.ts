import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Genie frontend build config. Talks only to the Genie backend API
// (VITE_GENIE_API_BASE_URL) - never to Azure AI Foundry directly.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    css: true,
    // Default `pool: "threads"` runs each test file in a worker_thread that
    // shares the main process's V8 heap ceiling - with ~14 files each
    // mounting Fluent UI + jsdom trees, that ceiling gets hit ("Worker
    // terminated due to reaching memory limit: JS heap out of memory").
    // Forks run each worker in its own child process (own OS-level heap)
    // and cap concurrency + give each fork more headroom.
    // Vitest 4 flattened `poolOptions.forks.*` into top-level `test` options
    // (see https://vitest.dev/guide/migration#pool-rework); `maxForks`
    // became `maxWorkers` and there is no longer a separate `minForks`.
    pool: "forks",
    maxWorkers: 2,
    execArgv: ["--max-old-space-size=4096"],
  },
});
