# Session — ERP-037 Live Deployment (GCP + Neon + Upstash + Modal)

Date: 2026-09-10
Tickets Touched: ERP-037

## Decisions

- Confirmed GCP `e2-micro` (from ADR-008's research) as the actual hosting choice via live testing,
  not just cost comparison — real memory headroom was measured under real running conditions before
  committing, matching this project's pattern all session of verifying rather than assuming.
- Deliberately deviated from ERP-037's original "docker-compose.yml or equivalent" acceptance
  criterion: ran the app directly via `uv run uvicorn` under systemd instead of Docker, because the
  VM's ~958MB total RAM has no real headroom for the Docker daemon's own overhead on top of the
  app itself. Systemd's `Restart=on-failure` + `enabled` satisfies the same underlying intent
  (auto-restart on failure/reboot) without that overhead.
- Used a free `sslip.io` hostname for TLS instead of purchasing a domain — Caddy gets a real Let's
  Encrypt cert for `<ip-with-dashes>.sslip.io`, at the cost of the URL breaking if the VM's IP ever
  changes (documented as a known tradeoff, not treated as a hidden gotcha).
- Modal's Ollama endpoint speaks the native Ollama HTTP API directly (no custom FastAPI proxy layer
  in front) — `app.generation.client.OllamaLLMClient`/`app.embedding.client.OllamaEmbeddingClient`
  needed zero code changes, just pointing their `ollama_host` settings at the Modal URL.
- Account/infra state (project IDs, connection endpoints, how-to-check, teardown steps) is tracked
  in `D:\github-projects\gcp-deployment-tracker.md`, outside this repo's git history — consistent
  with ADR-008's decision that cross-project infra state doesn't belong inside any single repo.

## Implementation Summary

- Provisioned GCP project `self-hosted-rag-platform`, linked billing, enabled Compute Engine,
  created firewall rule `allow-web` (ports 80/443/8000), created `e2-micro` VM `rag-platform-host`
  in `us-central1-a` (30GB `pd-standard` disk).
- Installed `uv`, `git`, Caddy on the VM (no Docker — see Decisions above); cloned the repo at
  `develop`.
- **Found and fixed a real memory bug via live testing, not caught by any test suite**: `import
  app.main` alone used 614MB/958MB RAM (64%) because `app/ingestion/parsers.py` imported `docling`
  (an ingestion *fallback* path, rarely reached) eagerly at module level, forcing every process
  start to pay `torch`/`transformers`'s import cost. Moved the import inside `parse_quality()` —
  verified via `tests/ingestion/test_parsers.py`'s real docling-fallback tests (still 6 passed),
  committed as `perf: lazy-import docling to cut baseline memory footprint` (`5be2c76`). Cut live
  uvicorn RSS from 509MB to 268MB, taking system-available RAM from 67MB to 373MB — this is what
  made `e2-micro` viable at all, not just theoretically survivable.
- Generated a real `AUTH_JWT_SECRET_KEY` on the VM; wrote `~/app/.env` (`chmod 600`, never
  committed).
- Set up `rag-platform.service` (systemd unit, `EnvironmentFile=~/app/.env`, `Restart=on-failure`,
  `enabled`) and `caddy.service` (`/etc/caddy/Caddyfile` reverse-proxying to `localhost:8000`,
  TLS via `34-31-5-88.sslip.io`) — both verified running and serving `200`.
- Neon (Postgres): created project `self-hosted-rag-platform` (AWS US East 2), ran all 11 Alembic
  migrations against it from the local dev machine, wrote the pooled connection string
  (`DATABASE_URL`, `postgresql+psycopg://` scheme) into the VM's `.env`. Verified via
  `POST /auth/register` on the live app returning `201` with a real persisted row.
- Upstash (Redis): created database (GCP `us-central1`, matching the VM's own region/cloud,
  eviction enabled since this is a pure cache). Discovered and fixed a real config gap before it
  silently no-op'd: there is no single `REDIS_URL` env var — `EmbeddingSettings`,
  `RetrievalSettings`, and `AuthSettings` each have their own `env_prefix` (`EMBEDDING_`,
  `RETRIEVAL_`, `AUTH_`), so all three prefixed vars needed setting, all pointing at the same
  Upstash instance. Verified via a direct `redis.Redis.from_url(...).ping()` (`True`) and a real
  `POST /auth/login` -> `POST /auth/refresh` flow (both `200`), exercising the revocation cache.
- Modal: authenticated the CLI locally (`modal setup`, browser OAuth, no manual token needed).
  Wrote `deploy/modal_ollama.py` — an `@app.cls(gpu="T4")` with `@modal.web_server(port=11434)`
  exposing Ollama directly, `gemma3:4b` + `nomic-embed-text` baked into the image at build time.
  Two real bugs found and fixed via actually deploying (not caught by reading a reference
  tutorial): (1) a reference implementation's hardcoded `ollama-linux-amd64.tgz` download URL is
  stale — Ollama repackaged to `.tar.zst` sometime after that tutorial was written; switched to
  Ollama's own install script instead of a hardcoded asset URL/format, so this doesn't recur next
  time Ollama repackages. (2) Ollama binds `127.0.0.1` by default, invisible to Modal's reverse
  proxy from outside that container namespace (`ConnectionRefusedError`, Modal's own error message
  points at the fix) — set `OLLAMA_HOST=0.0.0.0:11434` explicitly. Verified via `GET /api/tags`
  returning both models correctly.
- Wired `GENERATION_OLLAMA_HOST`/`EMBEDDING_OLLAMA_HOST`/`GENERATION_MODEL`/`EMBEDDING_MODEL` on
  the VM to the Modal endpoint; confirmed via `uv run python -m app.core.check_models` (ERP-034's
  tool, now proving its value in a real deployment) — both `OK`.
- **Full end-to-end pipeline test on the live deployment**: a real chunk about the Eiffel Tower
  ingested via `embed_and_persist`, retrieved via `search()`, answered via the live Modal endpoint
  — "The Eiffel Tower is located in Paris, France [1]." Correct, grounded, cited.
- Along the way, found and fixed a scripting gotcha worth remembering: bash's `source .env` mis-
  parses `DATABASE_URL` because it contains an unquoted `&` (query-string `&channel_binding=`) —
  bash treats bare `&` as a background-job operator regardless of surrounding non-whitespace,
  silently truncating the variable. The systemd service itself is unaffected (`EnvironmentFile=`
  uses systemd's own parser), but any one-off debugging script must load `.env` in Python instead.
  Documented in the tracker doc so it isn't rediscovered painfully next time.

## Blockers

None. ERP-037's last unchecked acceptance criterion (smoke-test at ~10-20 simulated concurrent
requests) remains — everything verified this session was correctness/connectivity at low
(single-request) concurrency, not a load test.

## Next Steps

- Run the concurrent-load smoke test to close out ERP-037 fully.
- Re-run ERP-029's retrieval-quality evaluation harness against live Ollama now that a real Ollama
  endpoint (via Modal) is reachable — still unconfirmed since ERP-031's per-owner FAISS
  partitioning.
- Set a reminder before the GCP free trial credit expires (2026-12-10, see the tracker doc) to
  decide explicitly whether to continue or tear down, rather than letting it silently convert to
  paid billing.
