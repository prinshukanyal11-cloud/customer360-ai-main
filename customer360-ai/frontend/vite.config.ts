/// <reference types="vitest" />
import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    // No /api proxy here on purpose. The axios client is built with an absolute
    // baseURL (API_BASE in src/services/api.ts), so nothing ever requests a
    // relative /api path. The proxy only appeared to work: a stale request would
    // be forwarded to port 8000 regardless of which API the app was configured
    // for, which is a confusing way to debug a misconfigured VITE_API_URL.
  },
  test: {
    globals: true,
    environment: "jsdom",
    // Scoped to src on purpose. Vitest's default glob also matches
    // e2e/*.spec.ts, which are Playwright suites: they import `test` from
    // @playwright/test, so vitest would collect them and fail trying to run a
    // browser-less copy of a browser test.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});