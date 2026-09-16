import { createContext, useContext, useState, type ReactNode } from "react";
import { apiFetch } from "./apiClient";
import { clearTokens, getTokens, setTokens } from "./tokenStorage";
import type { TokenResponse } from "./types";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => getTokens() !== null);

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
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
