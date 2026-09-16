# Web UI — Design Spec

Status: Approved (brainstorming)
Date: 2026-09-16
Related roadmap item: `docs/roadmap.md` "Web UI"; `.ai/tickets/ERP-043.md`

## Purpose

Every capability built so far (ingestion, hybrid retrieval, reranking, streaming grounded
generation, conversation memory, auth, admin) is API-only — usable via `curl`/Swagger, but not a
usable or presentable product. This adds a real frontend over the existing FastAPI backend so the
platform is both usable day-to-day and demoable, before moving on to the next portfolio project
(Agentic AI / LLMOps).

Goal is explicitly both usable *and* presentable — not a bare-minimum API client, but also not a
feature-complete product. Scope is deliberately the smallest slice that demonstrates the backend's
real capabilities end-to-end: log in, upload a document, ask about it, get a cited streamed answer.

## Scope

In scope:
- Local email/password login/register (backend already supports this, ERP-026)
- Chat: send a query, see a streamed, cited answer (backend: `POST /generation/query/stream`, ERP-020)
- A sidebar of the last 5 conversations, tracked client-side (no backend change)
- Document upload + ingestion status polling (backend: `POST /ingestion/pdf`, `GET /ingestion/jobs/{id}`)
- A small, necessary backend change: CORS (`CORSMiddleware`), required for any browser-based SPA
  calling this API cross-origin — not a feature choice, a structural requirement

Out of scope (deferred, each a clean fast-follow once the core app is proven out):
- "Continue with Google" OIDC login button — `GET /auth/oidc/google/callback` currently returns
  raw JSON, not a redirect to a frontend URL; wiring it up needs its own backend change
  (configurable frontend redirect target carrying the token pair) that doesn't belong bundled into
  a frontend-scoped ticket
- Admin UI (list/disable/revoke/delete users) — stays API/Swagger-only; low demo value, and the
  one admin (the user) is already comfortable there
- `httpOnly` cookie-based token storage — the backend is JSON-only (ERP-026), no cookie support
  exists; `localStorage` chosen instead (see Auth below)
- A backend "list my conversations"/"list my documents" endpoint — client-side `localStorage`
  tracking covers the last-5 requirement without backend scope creep
- End-to-end/browser test automation (Playwright etc.) — manual verification against the live
  backend is this repo's established verification culture; not worth a maintained e2e suite at
  this scope

## Approaches Considered

1. **Vite + React SPA on Vercel** (chosen) — no SSR/routing machinery this app doesn't need (it's
   100% behind login, zero SEO surface). Deploys to Vercel's free tier with zero config. Talks to
   the FastAPI backend directly over HTTPS.
2. **Next.js on Vercel** — same hosting outcome, but SSR/App Router/server actions are unused
   complexity for a 3-screen SPA-shaped app. Rejected as more machinery than the problem needs.
3. **Server-rendered HTML from FastAPI itself (Jinja2)** — no separate frontend project, but adds
   template rendering and static-asset serving to the already memory-tight e2-micro VM (the exact
   constraint ERP-037's lazy-docling-import fix existed to relieve), and means hand-rolling
   streaming/upload interactivity in vanilla JS. Rejected.

## Architecture

A new top-level `frontend/` directory in this repo (versioned alongside the API it depends on, but
deployed independently). Vite + React + TypeScript, Tailwind + shadcn/ui for components. No
backend-for-frontend layer — the SPA calls the live FastAPI backend directly. Three routes:
`/login`, `/chat`, `/documents`, behind an auth guard redirecting to `/login` when no valid token
is present. Deployed to Vercel (free tier — flagged for exactly this purpose in
`D:\github-projects\infrastructure-options.md`'s app-hosting section, which ruled Vercel out for
the backend itself but noted it as "excellent for a future frontend").

## Components

- **`apiClient`** — thin `fetch` wrapper. Base URL from a build-time env var
  (`VITE_API_BASE_URL`). Attaches `Authorization: Bearer <access_token>`. On a `401`, calls
  `POST /auth/refresh` once and retries the original request; if refresh also fails, clears
  `localStorage` and redirects to `/login`.
- **`AuthContext`** — holds `{access_token, refresh_token, user}`, backed by `localStorage`.
  Exposes `login`, `register`, `logout`. `localStorage` chosen over in-memory-only + `httpOnly`
  cookies because the latter needs backend cookie support that doesn't exist yet (ERP-026 is
  JSON-only) — same "don't bundle backend work into this ticket" reasoning applied elsewhere in
  this spec. Acceptable tradeoff at this scope: ~20 trusted users, no third-party scripts or
  user-generated HTML rendered anywhere in the app (the XSS surface `localStorage` token storage
  is normally weighed against).
- **`ChatPage`** — sidebar (last 5 conversations from `localStorage`, most-recent-first, each
  labeled by its first user message truncated) + message thread + input box. New chat mints a
  UUID client-side as `conversation_id` (matches the backend's client-supplied-ID design, ERP-018).
  Sends `POST /generation/query/stream`, consumes the SSE stream, renders citations.
- **`DocumentsPage`** — upload form (`POST /ingestion/pdf`) + a polling hook checking
  `GET /ingestion/jobs/{id}` every ~2s until `DONE`/`FAILED`, plus a list of this session's
  uploads (`localStorage`-tracked, same reasoning as conversations — no "list my documents"
  endpoint exists).

## Data Flow

1. **Login**: submit email/password → `POST /auth/login` → store `{access_token, refresh_token}`
   in `localStorage` → `AuthContext` updates → redirect to `/chat`.
2. **Chat**: on the first message of a new chat, mint a UUID client-side as `conversation_id`;
   every message → `POST /generation/query/stream` → consume the SSE stream (`citations` event
   first, then `token` events appended live, then a terminal `done`/`error`) → on `done`, upsert
   `{id, title: <first message, truncated>, last_updated}` into the `localStorage` conversation
   list, trimmed to the 5 most recent.
3. **Documents**: select a PDF → `POST /ingestion/pdf` → get `job_id` → poll
   `GET /ingestion/jobs/{job_id}` every ~2s until `DONE`/`FAILED` → reflect status in the UI;
   completed uploads get the same `localStorage`-list treatment as conversations.
4. **Token refresh**: `apiClient` catches any `401`, calls `POST /auth/refresh` once, retries the
   original request; if refresh also fails, clears `localStorage` and redirects to `/login`.

## Error Handling

- Network/API errors surface as an inline toast.
- A `FAILED` ingestion job shows its error detail in the Documents list.
- A terminal `error` SSE event during streaming renders as an error bubble in the chat thread,
  preserving whatever answer text already streamed in rather than discarding it.

## Backend Changes Required

Exactly one, and it's structural rather than a feature: add `CORSMiddleware` to `app/main.py`
allowing the deployed frontend's origin (the Vercel domain). No other backend endpoint, schema, or
behavior changes as part of this ticket.

## Testing

`Vitest` + `React Testing Library` for the pieces with real logic: `apiClient`'s
401-refresh-retry-once behavior, the conversation/document list's trim-to-5 logic, and SSE-stream
event parsing (citations → tokens → done/error). No end-to-end/browser test automation for v1 —
manual verification against the live backend before calling this done, consistent with this
repo's "verify against the real live thing" discipline (`CLAUDE.md`).

## Future Follow-ups

- "Continue with Google" OIDC login button, once the backend callback redirects to a configurable
  frontend URL instead of returning raw JSON.
- Admin UI screen, if API/Swagger-only admin workflows become a real friction point.
- A backend "list my conversations"/"list my documents" endpoint, if cross-device history access
  is ever needed (current `localStorage`-only tracking is single-browser).
- `httpOnly` cookie-based token storage, if the XSS-risk tradeoff of `localStorage` ever stops
  being acceptable (e.g. the app grows to render any third-party or user-generated content).
