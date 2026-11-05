/**
 * Vite already exposes `import.meta.env` via the `vite/client` types (declared
 * in tsconfig.json). This file only names the variables this app reads, so a
 * typo like `VITE_APIY_URL` is a compile error rather than an undefined lookup
 * that silently falls back to the default.
 */
interface ImportMetaEnv {
  /** Base URL of the backend API, e.g. http://localhost:8000/api/v1 */
  readonly VITE_API_URL?: string;
}