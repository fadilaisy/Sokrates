/** Login session for the cockpit. The server decides what each role may do; this only carries the token. */

export interface AuthUser {
  username: string;
  name: string;
  role: string;
  role_label: string;
  can_approve: boolean;
  /** How the user appears in the audit ledger, e.g. "Budi Santoso — Kepala Maintenance (@budi)". */
  signature: string;
}

interface Session {
  token: string;
  expires_at: string;
  user: AuthUser;
}

const KEY = "sf.session";

function read(): Session | null {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as Session | null;
    if (!s?.token || new Date(s.expires_at).getTime() < Date.now()) return null;
    return s;
  } catch {
    return null;
  }
}

let session: Session | null = read();

export function authToken(): string | null {
  return session?.token ?? null;
}

export function currentUser(): AuthUser | null {
  return session?.user ?? null;
}

export function saveSession(s: Session): void {
  session = s;
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* private mode: logged in for this visit only */
  }
}

export function clearSession(): void {
  session = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** Adds ?token= to a URL that can't carry a header (WebSocket, file link). */
export function withToken(url: string): string {
  const t = authToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

/** Fired when the server answers 401, so the app can show the login screen again. */
export const AUTH_EXPIRED_EVENT = "sf-auth-expired";
