import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { startActivityTracking } from "./activityTracker";
import { apiFetch, SESSION_EXPIRED_EVENT } from "./apiClient";
import { clearStoredUserId, getStoredUserId, setStoredUserId } from "./tokenStorage";
import type { AuthActionResponse, UserResponse } from "./types";

interface AuthContextValue {
  isAuthenticated: boolean;
  /**
   * The one piece of identity data stored client-side (ERP-116) -- previously decoded straight
   * from the access token's JWT payload, but that token is now an `httpOnly` cookie invisible
   * to this code by design. `/auth/login`/`/register`/`/refresh` return it explicitly in their
   * JSON body instead. Never blocks: available synchronously in the same render as
   * `isAuthenticated`, same guarantee the old JWT-decode version made.
   */
  userId: string | null;
  /**
   * The full profile from `GET /auth/me`, fetched best-effort after login/on mount. Purely
   * cosmetic (e.g. showing the logged-in user's email) -- nothing should block on this being
   * non-null, since a slow or failed fetch must never hang the rest of the app.
   */
  user: UserResponse | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // ERP-116: an optimistic initial guess -- a stored user ID means *some* session existed
  // recently, not proof the httpOnly cookies are still valid (they may have naturally expired,
  // or a stale marker could survive a hard-crash without ever calling `logout()`). Corrected
  // reactively the moment the first real request either succeeds or exhausts `apiFetch`'s
  // refresh attempt and fires SESSION_EXPIRED_EVENT (see the effect below).
  const [isAuthenticated, setIsAuthenticated] = useState(() => getStoredUserId() !== null);
  const [userId, setUserId] = useState<string | null>(() => getStoredUserId());
  const [user, setUser] = useState<UserResponse | null>(null);

  async function fetchCurrentUser(): Promise<void> {
    try {
      const response = await apiFetch("/auth/me");
      if (!response.ok) return;
      setUser((await response.json()) as UserResponse);
    } catch {
      // Best-effort only -- the email display in the header just stays blank. Nothing in the
      // app depends on `user` being populated (see `userId`'s docstring above).
    }
  }

  // Resolves the full profile on a hard page reload (a user ID marker already in localStorage,
  // but the `user` object itself was never persisted -- avoids storing profile data that could
  // go stale relative to the backend).
  useEffect(() => {
    if (isAuthenticated) void fetchCurrentUser();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ERP-116: idle-timeout tracking only matters once actually logged in (the login page itself
  // has nothing to time out). `apiClient.ts`'s `tryRefresh` fires SESSION_EXPIRED_EVENT the
  // moment it gives up on the session (idle timeout, or a genuinely failed refresh) -- without
  // this listener, `isAuthenticated` would only update on the next full remount, leaving stale
  // UI up instead of redirecting to /login immediately.
  useEffect(() => {
    if (!isAuthenticated) return;
    startActivityTracking();
    const onSessionExpired = () => logout();
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated]);

  async function login(email: string, password: string): Promise<void> {
    const response = await apiFetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      throw new Error("Invalid email or password");
    }
    const body = (await response.json()) as AuthActionResponse;
    setStoredUserId(body.user_id);
    setIsAuthenticated(true);
    setUserId(body.user_id);
    void fetchCurrentUser();
  }

  async function register(email: string, password: string): Promise<void> {
    const response = await apiFetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      throw new Error("Registration failed (email may already be registered)");
    }
    await login(email, password);
  }

  function logout(): void {
    // Best-effort: revokes the refresh token server-side and clears the cookies. Client state
    // (below) updates immediately regardless of whether this network call succeeds, since the
    // user's intent to leave shouldn't wait on it.
    void apiFetch("/auth/logout", { method: "POST" });
    clearStoredUserId();
    setIsAuthenticated(false);
    setUserId(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, userId, user, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
