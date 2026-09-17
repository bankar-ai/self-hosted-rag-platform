# .ai/tickets/

Purpose: work management. One file per ticket (e.g. `ERP-001.md`).

The ticket format and framework are defined in **ERP-002 — Ticket Framework**. Use the template at `.ai/templates/ticket.md` to create new tickets.

Status lifecycle: `Backlog` -> `In Progress` -> `Done`. Tickets may declare a `Depends On` field listing blocking ticket IDs.

## Category (optional field)

Added 2026-09-17, after a live UI review surfaced a batch of tickets in one sitting (ERP-044
through ERP-046) and it became useful to tell at a glance what kind of gap each one represents:

- **Bug** — the code doesn't do what it was already supposed to do; a defect against the
  existing spec/design (e.g. wrong output, a crash, data leaking across users).
- **Improvement** — the code works exactly as designed; this is a new capability or a better
  version of an existing one, not a defect (e.g. a feature nobody had scoped yet).
- **Lapse** — the code works exactly as designed, and nothing is "wrong" against that design,
  but the design itself missed a case that matters in practice (e.g. a guardrail nobody thought
  to add). Distinct from a Bug (there's no spec being violated) and from an Improvement (it's not
  optional polish — it's a gap worth closing).

Not retroactively applied to tickets predating this convention — only used going forward, and
only where the distinction is actually useful to note.
