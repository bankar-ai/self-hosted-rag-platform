import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";

// ERP-091: how long a login/register submission waits before showing the "this can take a
// while" hint. Chosen to sit above a normal warm-DB round-trip (well under a second) but well
// below Neon's observed cold-start range (3-5+s per live feedback), so the hint only appears
// when it's actually relevant, not on every ordinary login.
const SLOW_SUBMIT_HINT_DELAY_MS = 2000;

export default function LoginPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showSlowHint, setShowSlowHint] = useState(false);

  // Best-effort ping to nudge Neon's scale-to-zero compute awake while the user is still
  // typing credentials (ERP-091) -- by the time they submit, the DB may already be warm.
  // Ignored entirely if it fails; the login/register call below pays the cold-start cost
  // itself either way, this is purely a head start.
  useEffect(() => {
    void apiFetch("/health").catch(() => {
      // Best-effort only -- see comment above.
    });
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    const slowHintTimer = setTimeout(() => setShowSlowHint(true), SLOW_SUBMIT_HINT_DELAY_MS);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      clearTimeout(slowHintTimer);
      setIsSubmitting(false);
      setShowSlowHint(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-lg font-semibold tracking-tight text-slate-900">
          Self-Hosted RAG Platform
        </p>
        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
        >
          <h1 className="text-xl font-semibold text-slate-900">
            {mode === "login" ? "Log in" : "Register"}
          </h1>
          <label className="flex flex-col gap-1 text-sm text-slate-700">
            Email
            <Input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              disabled={isSubmitting}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-slate-700">
            Password
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={8}
              disabled={isSubmitting}
            />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          {showSlowHint && (
            <p className="text-sm text-slate-500">
              This can take a few seconds if the app has been idle for a while...
            </p>
          )}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting
              ? mode === "login"
                ? "Logging in..."
                : "Registering..."
              : mode === "login"
                ? "Log in"
                : "Register"}
          </Button>
          <button
            type="button"
            className="text-sm text-slate-500 underline hover:text-slate-900"
            disabled={isSubmitting}
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? "Need an account? Register" : "Already have an account? Log in"}
          </button>
        </form>
      </div>
    </div>
  );
}
