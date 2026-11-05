import type { User } from "@/types";

interface StoredSession {
  user: User | null;
}

const KEY = "customer360_session";

export function loadSession(): StoredSession {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : { user: null };
  } catch {
    return { user: null };
  }
}

export function saveUser(user: User | null): void {
  localStorage.setItem(KEY, JSON.stringify({ user }));
}

export function clearSession(): void {
  localStorage.removeItem(KEY);
}