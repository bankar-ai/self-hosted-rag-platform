import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { apiFetch } from "./apiClient";
import { clearTokens, getTokens, setTokens } from "./tokenStorage";
import type { TokenResponse, UserResponse } from "./types";

interface AuthContextValue {
  isAuthenticated: boolean;
  user: UserResponse | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => getTokens() !== null);
  const [user, setUser] = useState<UserResponse | null>(null);

  async function fetchCurrentUser(): Promise<void> {
    const response = await apiFetch("/auth/me");
    if (!response.ok) return;
    setUser((await response.json()) as UserResponse);
  }

  // Resolves who's logged in on a hard page reload (tokens already in localStorage, but the
  // `user` object itself was never persisted -- avoids storing profile data in localStorage
  // that could go stale relative to the backend).
  // Intentionally run once on mount only -- `login`/`register` already call
  // `fetchCurrentUser()` directly after a fresh sign-in, this effect only covers the
  // page-reload case where tokens already exist in localStorage.
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
    await fetchCurrentUser();
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
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, user, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
