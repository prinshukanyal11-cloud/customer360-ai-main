import axios from "axios";
import type { ApiError, TokenResponse } from "@/types";

// Read from the environment rather than hardcoded. `VITE_API_URL` is documented in
// .env.example, and before this it was never read: pointing the app at a
// different API host required editing source. The fallback keeps a fresh clone
// working with no .env at all.
export const API_BASE: string =
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

// No `Content-Type` here, deliberately.
//
// Setting a default `Content-Type: application/json` on the instance overrides
// the header for *every* request, including file uploads. When axios sees
// FormData under a forced application/json header it runs formToJSON() on the
// body instead of streaming it, so the browser sends
//   {"file":{}}
// with no boundary and no filename - the File has no enumerable JSON
// properties, so it serialises as an empty object. FastAPI then parses the body
// as JSON and answers 422 "Field required" for `file`.
//
// Axios already sets application/json itself for plain objects, so JSON calls
// are unaffected by leaving this off, and FormData is left for the browser to
// encode as multipart/form-data with a boundary.
const client = axios.create({
  baseURL: API_BASE,
});

client.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// A single in-flight refresh, shared by every request that gets a 401.
// Without this, five panels loading at once would each spend the stored refresh
// token. The backend rotates refresh tokens and treats a replay as a possible
// leak, revoking the whole family - so the duplicates would sign the user out.
let refreshInFlight: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  const refresh = localStorage.getItem("refresh_token");
  if (!refresh) {
    throw new Error("No refresh token stored.");
  }
  // A bare axios call, not `client`: going through the instance would re-enter
  // this interceptor and recurse.
  const res = await axios.post<TokenResponse>(`${API_BASE}/auth/refresh`, {
    refresh_token: refresh,
  });
  setTokens(res.data.access_token, res.data.refresh_token);
  return res.data.access_token;
}

export function isRefreshEndpoint(url?: string): boolean {
  return Boolean(url?.includes("/auth/refresh"));
}

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error?.config as (typeof error.config & { _retried?: boolean }) | undefined;
    const status = error?.response?.status;

    // The stored refresh token is spent or revoked: there is nothing to retry
    // with, so hand the failure to the app, which routes to login.
    if (status === 401 && original && !original._retried && !isRefreshEndpoint(original.url)) {
      original._retried = true;
      refreshInFlight = refreshInFlight ?? refreshAccessToken().finally(() => {
        refreshInFlight = null;
      });
      try {
        const access = await refreshInFlight;
        original.headers = { ...original.headers, Authorization: `Bearer ${access}` };
        return client(original);
      } catch {
        // Refresh failed, so the session is genuinely over. Clearing here rather
        // than leaving a stale pair in storage means a later request does not
        // retry against a token the server has already retired.
        clearTokens();
      }
    }

    const serverError = error?.response?.data as ApiError | undefined;
    if (serverError?.error?.message) {
      error.message = serverError.error.message;
    }
    return Promise.reject(error);
  },
);

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem("access_token", access);
  localStorage.setItem("refresh_token", refresh);
}

export function clearTokens(): void {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

export default client;