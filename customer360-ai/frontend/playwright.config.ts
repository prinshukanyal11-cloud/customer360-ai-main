import { defineConfig, devices } from "@playwright/test";
import { API_URL, BACKEND_PORT, BASE_URL, FRONTEND_PORT } from "./e2e/backend-url";

/**
 * Browser-level end-to-end tests.
 *
 * These exist for the flows that neither the vitest unit tests nor the HTTP smoke
 * script can reach: the axios refresh interceptor, and what the app does when
 * the server rejects a token. Everything else stays cheaper at the HTTP boundary.
 *
 * The backend runs on SQLite against a throwaway file, seeded from the model
 * metadata, so no PostgreSQL is needed to run this locally.
 */

// Overridable so CI can point at an interpreter on PATH (or an activated venv)
// without hardcoding a platform-specific path here. Quoted below because the
// path can contain spaces, and an unquoted "D:\Customer360 AI\..." is not a
// command the shell can resolve.
const PYTHON = process.env.PYTHON ?? "python";

export default defineConfig({
  testDir: "./e2e",
  // The backend seeds data through the API, so tests share one server; running
  // them in parallel would interleave registrations.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      // The app does not create its own tables (Alembic owns the schema), so the
      // E2E database is built from the models first, then the API is served
      // against it. `&&` so a failed prepare stops the suite starting.
      command: `"${PYTHON}" scripts/e2e_prepare_db.py && "${PYTHON}" -m uvicorn app.main:app --port ${BACKEND_PORT} --log-level warning`,
      cwd: "../backend",
      url: `${API_URL}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        // A throwaway database so the suite never touches a developer's data.
        DATABASE_URL: "sqlite:///./.playwright-e2e.db",
        DATABASE_URL_RO: "sqlite:///./.playwright-e2e.db",
        // Off, or every test from the same address would exhaust the budget.
        RATE_LIMIT_ENABLED: "false",
        // The browser loads from the preview server, so that is the only origin
        // allowed to make credentialed cross-origin requests.
        CORS_ORIGINS: BASE_URL,
        LLM_PROVIDER: "mock",
        LOGIN_LOCKOUT_ENABLED: "false",
        // Any real-looking secret: the production guard rejects short ones.
        SECRET_KEY: "e2e-secret-key-not-used-for-real-traffic",
        JWT_SECRET: "e2e-secret-key-not-used-for-real-traffic",
        PYTHONPATH: ".",
      },
    },
    {
      // Built rather than served from source, so the suite exercises the same
      // bundle a deployment would ship - including the baked-in API URL.
      command: `npm run build && npx vite preview --port ${FRONTEND_PORT} --strictPort`,
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      env: {
        VITE_API_URL: API_URL,
      },
    },
  ],
});