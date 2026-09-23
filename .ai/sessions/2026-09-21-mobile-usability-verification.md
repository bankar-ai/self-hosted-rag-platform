# Session — Mobile Usability Verification (ERP-085)

Date: 2026-09-21
Tickets Touched: ERP-085

## Decisions

- Verify-then-fix, split across sessions: this session only verifies and logs findings on a
  real device; fixing is explicitly deferred to next session due to weekly usage quota (~80%
  used, resets in ~2 days).

## Implementation Summary

No code changes. Walked through the live app (`bankar-ai-self-hosted-rag-platform.vercel.app`)
on a real Android phone (Chrome) per ERP-085's acceptance criteria: opened/closed the sidebar
overlay, sent a chat message, opened a citation's source panel, closed it. User supplied
screenshots of each step, reviewed and diagnosed here.

Findings (full detail in `.ai/tickets/ERP-085.md`'s Verification section):

1. Top nav/header wraps and visually collides with the page title on narrow viewports.
2. Page-level horizontal scroll is required to reach Documents/email/Log out — violates
   ERP-078's own "no horizontal scroll of the page body" acceptance criterion; the header was
   evidently never covered by that responsive pass.
3. `SourcePanel` chunk text overflows horizontally instead of wrapping.
4. `SourcePanel`'s Copy/Close controls are off-screen by default as a result of #3 — a real
   tap-target reachability problem.
5. Sidebar overlay itself (New chat, recent conversations, document checklist) is fully usable
   — no issues found there.

Likely shared root cause for #1–4: fixed-width/`nowrap` flex rows in the header and in
`SourcePanel` never given `flex-wrap`/`min-w-0`/`break-words` treatment.

## Blockers

None — clear path to fix, just deferred for quota reasons.

## Next Steps

- Fix the header/nav to collapse into a compact mobile layout (logo/title truncates or
  shrinks; nav collapses behind the existing hamburger Menu), eliminating page-level
  horizontal scroll entirely.
- Fix `SourcePanel` text rendering to wrap instead of overflow, and keep Copy/Close reachable
  without horizontal scroll (e.g. a sticky header row within the panel).
- Re-check chat bubbles and the document-checklist panel at an even narrower width (<360px)
  since this pass used one device size.
- Update `.ai/tickets/ERP-085.md`'s remaining acceptance criterion and status once fixed.
