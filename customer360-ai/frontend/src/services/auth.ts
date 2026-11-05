import client, { clearTokens, setTokens } from "@/services/api";
import type { TokenResponse, User } from "@/types";

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  full_name?: string;
  organization_name?: string;
}

async function login(payload: LoginPayload): Promise<TokenResponse> {
  const res = await client.post<TokenResponse>("/auth/login", payload);
  setTokens(res.data.access_token, res.data.refresh_token);
  return res.data;
}

async function register(payload: RegisterPayload): Promise<TokenResponse> {
  const res = await client.post<TokenResponse>("/auth/register", payload);
  setTokens(res.data.access_token, res.data.refresh_token);
  return res.data;
}

async function me(): Promise<User> {
  const res = await client.get<User>("/auth/me");
  return res.data;
}

/**
 * End the session on the server, not just in this tab.
 *
 * The backend keeps a record of every issued refresh token, and logout revokes
 * them. Clearing localStorage alone would leave a token that still works for its
 * full lifetime - which is the difference between signing out and hiding the
 * sign-in form.
 *
 * Tokens are cleared either way: a network failure must not strand the user in a
 * half-signed-in state, and a revoked-on-the-server token left in storage is
 * harmless because the refresh call would reject it.
 */
async function logout(): Promise<void> {
  try {
    await client.post("/auth/logout");
  } finally {
    clearTokens();
  }
}

export const authService = { login, register, me, logout };