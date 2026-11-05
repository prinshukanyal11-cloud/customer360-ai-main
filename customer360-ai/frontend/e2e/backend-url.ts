/**
 * Single source of truth for the ports the E2E stack uses, imported by both the
 * Playwright config and the specs. Duplicating the number in two files is how a
 * suite ends up asserting against a port nothing is listening on.
 */

/** Backend under test. Not 8000, so a developer's dev server is never disturbed. */
export const BACKEND_PORT = 8093;

/**
 * Preview server. Pinned to 5173 because that is the origin already in the
 * backend's CORS allow-list; Vite's default 4173 would need the CORS defaults
 * widened for a test-only port.
 */
export const FRONTEND_PORT = 5173;

export const API_URL = `http://localhost:${BACKEND_PORT}/api/v1`;
export const BASE_URL = `http://localhost:${FRONTEND_PORT}`;