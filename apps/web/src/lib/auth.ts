/** Client-side auth session (localStorage JWT). */

export type AuthUser = {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
  created_at?: string | null;
};

export type AuthTokenResponse = {
  access_token: string;
  token_type: string;
  user: AuthUser;
};

const TOKEN_KEY = "anzai_access_token";
const USER_KEY = "anzai_user";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  // Lazy import to avoid circular deps with api → auth
  void import("@/lib/prefetch").then((m) => m.clearPrefetchCache()).catch(() => {});
}

export function isLoggedIn(): boolean {
  return Boolean(getAccessToken());
}
