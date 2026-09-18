import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { apiFetch } from "./apiClient";
import { decodeAccessTokenPayload } from "./jwt";
import { clearTokens, getTokens, setTokens } from "./tokenStorage";
import type { TokenResponse, UserResponse } from "./types";

interface AuthContextValue {
  isAuthenticated: boolean;
  /**
   * Decoded straight from the stored access token (synchronous, no network round-trip) --
   * this is what pages should use to scope per-user localStorage data. Never blocks: if a
   * caller needs a user ID to render, it's available in the same render as `isAuthenticated`.
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

function readUserIdFromStoredToken(): string | null {
  const tokens = getTokens();
  if (!tokens) return null;
  return decodeAccessTokenPayload(tokens.accessToken)?.sub ?? null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => getTokens() !== null);
  const [userId, setUserId] = useState<string | null>(() => readUserIdFromStoredToken());
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

  // Resolves the full profile on a hard page reload (tokens already in localStorage, but the
  // `user` object itself was never persisted -- avoids storing profile data that could go
  // stale relative to the backend).
  useEffect(() => {
    if (isAuthenticated) void fetchCurrentUser();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function login(email: string, password: string): Promise<void> {
    const response = await apiFetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      throw new Error("Invalid email or password");
    }
    const tokens = (await response.json()) as TokenResponse;
    setTokens({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
    setIsAuthenticated(true);
    setUserId(decodeAccessTokenPayload(tokens.access_token)?.sub ?? null);
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
    clearTokens();
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
