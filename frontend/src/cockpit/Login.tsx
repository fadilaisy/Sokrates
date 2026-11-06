import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { authApi, ApiError } from "../lib/api";
import { AUTH_EXPIRED_EVENT, clearSession, currentUser, saveSession, type AuthUser } from "../lib/auth";
import { Btn, LogoMark, Spinner } from "./ui";

interface AuthValue {
  /** Login is on for this server (AUTH_USERS set). */
  enabled: boolean;
  user: AuthUser | null;
  /** Approve buttons: true for Kepala Maintenance and Admin, or when login is off. */
  canApprove: boolean;
  logout: () => void;
}

const AuthContext = createContext<AuthValue>({ enabled: false, user: null, canApprove: true, logout: () => {} });

export const useAuth = () => useContext(AuthContext);

/** Shows the login screen when the server requires it; otherwise renders the app as before. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [user, setUser] = useState<AuthUser | null>(currentUser());

  useEffect(() => {
    let alive = true;
    authApi
      .config()
      .then((c) => alive && setEnabled(c.enabled))
      // Backend down or older without /api/auth: show the app; its own banner explains.
      .catch(() => alive && setEnabled(false));
    return () => {
      alive = false;
    };
  }, []);

  // A stored token the server no longer accepts (restart, role change) drops back to login.
  useEffect(() => {
    if (!enabled || !user) return;
    authApi.me().catch(() => undefined);
  }, [enabled, user]);

  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
  }, []);

  if (enabled === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-page">
        <Spinner className="size-8" />
      </div>
    );
  }
  if (enabled && !user) return <LoginScreen onLogin={setUser} />;

  const value: AuthValue = { enabled, user, canApprove: !enabled || !!user?.can_approve, logout };
  // key: a new login remounts the cockpit so it reconnects with the new token.
  return (
    <AuthContext.Provider value={value}>
      <div key={user?.username ?? "open"} className="contents">
        {children}
      </div>
    </AuthContext.Provider>
  );
}

function LoginScreen({ onLogin }: { onLogin: (u: AuthUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const s = await authApi.login(username.trim(), password);
      saveSession(s);
      onLogin(s.user);
    } catch (err) {
      setError(err instanceof ApiError && typeof err.detail === "string" ? err.detail : "Tidak bisa login. Cek koneksi ke server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-4">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-5 rounded-lg border border-line bg-surface p-8 shadow-sm">
        <div className="flex items-center gap-3">
          <LogoMark className="size-10" />
          <div>
            <h1 className="text-[22px] font-bold text-navy">SkillForge</h1>
            <p className="text-[15px] text-muted-ink">Masuk untuk membuka cockpit</p>
          </div>
        </div>
        <label className="flex flex-col gap-1">
          <span className="text-[15px] font-medium text-navy">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            className="min-h-12 rounded-md border border-line bg-surface px-4 text-[16px]"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[15px] font-medium text-navy">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            className="min-h-12 rounded-md border border-line bg-surface px-4 text-[16px]"
          />
        </label>
        {error && (
          <p role="alert" className="text-[15px] font-medium text-fault">
            {error}
          </p>
        )}
        <Btn type="submit" size="lg" disabled={busy || !username.trim() || !password}>
          {busy ? "Masuk…" : "Masuk"}
        </Btn>
        <p className="text-[14px] text-muted-ink">
          Hanya Kepala Maintenance dan Admin yang bisa menyetujui perubahan ke SAP dan PLC. Setiap persetujuan tercatat atas nama akun Anda.
        </p>
      </form>
    </div>
  );
}

/** Name, role and log-out button for the sidebar. Nothing when login is off. */
export function UserBadge({ className }: { className?: string }) {
  const { enabled, user, logout } = useAuth();
  if (!enabled || !user) return null;
  return (
    <div className={className}>
      <p className="text-[16px] font-bold text-navy">{user.name}</p>
      <p className="text-[14px] text-muted-ink">
        {user.role_label} · @{user.username}
      </p>
      <button type="button" onClick={logout} className="mt-1 text-[15px] text-navy underline hover:no-underline">
        Keluar
      </button>
    </div>
  );
}
