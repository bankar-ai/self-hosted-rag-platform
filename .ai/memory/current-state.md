# Current State

Living summary of what exists in this repository right now. Update in place as state changes — do not append history here (that belongs in `.ai/sessions/`).

## What Exists

- The `.ai/` AI Engineering Operating System is fully built out:
  - `tickets/` — ticket template + lifecycle (ERP-002), tickets ERP-001–013
  - `adr/` — ADR template + lifecycle (ERP-003), ADR-001 ("Adopt Architecture Decision Records"), ADR-002 ("Branch Strategy & CI Approach"), ADR-003 ("Data Layer & Caching Architecture"), ADR-004 ("Automated Secrets Scanning")
  - `sessions/` — session template + lifecycle (ERP-004), entries: 2026-07-22, 2026-08-01, 2026-08-02 (strengthen-engineering-guidelines), 2026-08-02 (tooling-config), 2026-08-06 (embedding-persistence), 2026-08-08 (retrieval-endpoint), 2026-08-26 (redis-embedding-cache), 2026-08-26 (bm25-hybrid-retrieval), 2026-08-26 (reranking), 2026-08-26 (pageindex-structure-aware-retrieval), 2026-08-31 (generation), 2026-09-01 (conversation-memory), 2026-09-01 (conversation-history-endpoint), 2026-09-04 (harden-embedding-cache), 2026-09-05 (search-vector-index-and-nonlocking-migration)
  - `memory/` — this framework (ERP-005)
- `docs/architecture.md`, `docs/engineering-guidelines.md`, `docs/roadmap.md` describe the intended project, philosophy, stack, and standards. `docs/architecture.md` also has the target-state architecture diagram (`docs/diagrams/architecture.drawio`/`.png`) and an explicit open-source/free-only dependency constraint.
- `CLAUDE.md` is a short operational guide pointing into `docs/` and `.ai/`; also documents the mandatory `uv` workflow, the research-before-recommending rule, the automated secrets-scanning guardrail, and the session-log/current-state maintenance habit.
- GitHub repository setup is done (ERP-008): `main` (protected, default branch) + `develop` branch model, CI (`.github/workflows/ci.yml`: gitleaks scan + `pytest`, verified actually green — not just present), PR template.
- Automated secrets-scanning guardrail is live (ERP-010): Gitleaks via `pre-commit`, local hook + CI backstop, verified to actually block a real secret.
- Ruff (lint) and Mypy (`--strict`, scoped to `app/`) are configured (ERP-007): Ruff runs via `pre-commit` and CI, Mypy via CI only. `app/` and `tests/` are fully compliant. `docs/engineering-guidelines.md` marks the now-enforced items accordingly.
- Pytest coverage gate is enforced (ERP-006): `pytest-cov`, `--cov-fail-under=90` in CI. A logging convention (stdlib `logging`, module-level loggers, `logger.exception` on non-re-raising excepts) is defined in `docs/engineering-guidelines.md` and applied to `app/ingestion/jobs.py`'s previously-silent failure path.
- **First vertical slice of application code exists and is live on `main`**: `app/ingestion/` — a PDF ingestion pipeline (`POST /ingestion/pdf` job-based upload, `GET /ingestion/jobs/{job_id}` polling). PyMuPDF4LLM fast-path parsing with automatic Docling fallback (tables/OCR). Structure-aware Markdown chunking with full provenance metadata (page range, section path, parser used). Full test suite under `tests/ingestion/` (33 tests, ~98.6% coverage), all passing in CI.
- **Embedding generation and durable persistence are wired into the ingestion job pipeline (ERP-011)**: Postgres persistence via SQLAlchemy 2.0 + Alembic (`app/core/db.py`, `app/ingestion/models.py`, `app/ingestion/repository.py` — document + chunks saved in one transaction), a FAISS index persisted to local disk (`app/embedding/`), and an Ollama-backed embedding client (Nomic Embed). `run_ingestion_job` calls the new `embed_and_persist` orchestration after chunking, inside the same try/except as the existing parse/chunk stage, so a `DONE` job now means chunks are embedded and durably persisted, not just held in memory. `docker-compose.yml` provides a local Postgres service; CI runs the full suite against a real Postgres service container. Merged to `develop` via PR #4 (merge commit `a019e35`).
- **A synchronous retrieval endpoint exists (ERP-012)**: `POST /retrieval/query` (new `app/retrieval/` module: `schemas.py`, `service.py`, `router.py`) embeds the query text and searches the FAISS index (via `FaissIndex.search` in `app/embedding/index.py`) for the nearest vectors, hydrating matches from Postgres (via `get_chunks_by_vector_ids` in `app/ingestion/repository.py`). `top_k` defaults to 5, bounded 1-50; an empty query is rejected with `422`; an empty index returns `200` with `results: []`. Vector-only ranking has since been superseded by hybrid ranking — see the ERP-014 bullet below (contract unchanged). Merged to `develop` via PR #5 (merge commit `2792e90`).
- **A Redis cache-aside layer sits in front of Ollama embedding calls (ERP-013)**: `app/embedding/cache.py`'s `RedisEmbeddingCache` (keyed by a length-prefixed sha256 hash of `model` + `text` so a colon in a model tag like `nomic-embed-text:latest` can't collide with a different `(model, text)` pair, TTL-bounded, degrades to a miss/no-op rather than raising if Redis is unreachable) is wired directly into `OllamaEmbeddingClient.embed` (`app/embedding/client.py`) — cache misses are batched to Ollama in one call, results written back, original order preserved. Both existing callers (`embed_and_persist` via `run_ingestion_job`, and `app/retrieval/service.py`'s `search`) get caching transparently with no changes to either module. `docker-compose.yml` and CI both gained a `redis:7-alpine` service so cache tests run against real Redis, not mocks. Merged to `develop` via PR #7 (merge commit `c3d90ff`).
- **`POST /retrieval/query` is hybrid (vector + BM25) retrieval (ERP-014)**: `chunks` gained a generated `search_vector` (`tsvector`) column and a GIN index (new Alembic migration `a97a8780506f`), and `app/ingestion/repository.py` gained `search_chunks_by_text` (Postgres full-text search via `plainto_tsquery`/`ts_rank`). `app/retrieval/service.py` fuses BM25 and FAISS vector rank-ordered results via Reciprocal Rank Fusion (RRF, `k=60` — see `.ai/adr/ADR-005.md`) into one ranked list. Each retriever is oversampled to `top_k * RRF_OVERSAMPLE_MULTIPLIER` (4x) candidates before fusion, not just `top_k`, so a chunk strong on one signal but just outside `top_k` on the other still gets a chance to fuse in. `RetrievedChunk.score` is the fused RRF score, normalized to `(0, 1]` (not `1/(1+distance)`, and not a raw similarity metric — see the field's description). Requires `alembic upgrade head` to run before deploying this code (the `search_vector` column must exist first). Endpoint request/response contract unchanged (additive only); still no per-document filtering. Merged to `develop` via PR #8 (merge commit `933dfdb`).
- **`POST /retrieval/query` supports optional reranking (ERP-015)**: `RetrievalQuery.rerank: bool = False` gates an optional cross-encoder pass (new `app/retrieval/reranker.py`: `Reranker` protocol + `FlashRankReranker`, backed by the new `flashrank` dependency's `ms-marco-TinyBERT-L-2-v2` ONNX model — no `torch`; see `.ai/adr/ADR-006.md`). When `rerank=True`, `service.search`'s fused BM25+vector results are re-scored/reordered by the cross-encoder before being returned, with `RetrievedChunk.score` reflecting the reranker's score. When `False` (the default), no reranker is constructed — zero added latency/cost, and ERP-014's behavior is unchanged. Merged to `develop` via PR #9 (merge commit `f7c3019`).
- **`POST /retrieval/query` supports optional structure-aware section expansion (ERP-016)**: `RetrievalQuery.expand_sections: bool = False` gates an optional post-processing step (`app/retrieval/service.py`'s `_expand_sections`, backed by a new `app/ingestion/repository.py`'s `get_sibling_chunks`) that, for each ranked chunk (after fusion and any reranking), inserts every other chunk in the same document sharing its exact `section_path` immediately after it, inheriting its score — surfacing full section context rather than isolated fragments. See `.ai/adr/ADR-007.md` for why this lighter-weight sibling-expansion approach was chosen over a real hierarchical-tree/reasoning-based PageIndex implementation. The response can legitimately exceed `top_k` when enabled (intended, not re-truncated). This closes out the three-ticket ERP-012 follow-up sequence (BM25 hybrid retrieval → reranking → PageIndex-style retrieval). Merged to `develop` via PR #10 (merge commit `ae69a5a`).
- **`POST /generation/query` synthesizes a grounded, cited answer over retrieval results (ERP-017)**: a new `app/generation/` module (`config.py`, `client.py`, `prompt.py`, `schemas.py`, `service.py`, `router.py`) calls `app.retrieval.service.search` unmodified (same `top_k`/`rerank`/`expand_sections` knobs, ERP-014/015/016 hybrid fusion behavior untouched), then synthesizes one answer via a local Ollama-hosted LLM (`GenerationSettings.model = "qwen3"`). `app/generation/prompt.py`'s `build_prompt` numbers ranked chunks `[1]..[n]` and truncates by character budget (`max_context_chars`, default 8000), always dropping the lowest-ranked tail rather than an arbitrary cut. The LLM is instructed (via a fixed `SYSTEM_PROMPT`) to cite inline as `[1]`, `[2]`, ... and to say explicitly when context is insufficient; `GenerationResponse` carries `answer: str` plus a parallel `citations: list[Citation]` for provenance. An empty retrieval result short-circuits to a fixed "not enough information" response without calling the LLM. Sync-only (no streaming) and no multi-turn memory — both deferred to `docs/roadmap.md`. No new dependency (`ollama` was already present via `app/embedding/client.py`). This closes the platform's generation gap named in prior "What Does Not Exist Yet" entries.
- **`POST /generation/query` supports multi-turn conversation memory (ERP-018)**: an optional `conversation_id: UUID | None` on `GenerationQuery` acts as a stateless/stateful switch — omitted, the endpoint is byte-for-byte identical to ERP-017's single-turn behavior (no DB session opened, nothing persisted, `response.conversation_id = null`); provided, it's a client-supplied UUID with get-or-create semantics (no server-generated IDs, no "unknown ID" error case). Two new tables, `conversations` and `conversation_messages` (new `app/generation/models.py`, sharing the existing `Base` from `app/ingestion/models.py`; `conversation_messages` carries a monotonic `sequence` column, `Identity(always=True)`, for deterministic ordering — `created_at` alone is unreliable because Postgres's `now()` is the transaction-start timestamp and both turns of an exchange commit in one transaction), accessed via new `app/generation/repository.py` (`get_or_create_conversation`, `append_message`, `get_recent_messages`). From the second turn onward, `app/generation/rewrite.py`'s `rewrite_query` (reusing the existing `LLMClient`, no separate rewrite-model setting) rephrases the follow-up into a standalone query for retrieval only — the raw text is what's persisted and what the LLM sees in the prompt's history section. History is windowed to `GenerationSettings.history_window_turns` (default 6) messages, rendered chronologically before the numbered context block in `app/generation/prompt.py`'s `build_prompt`. Both turns are persisted in one transaction, only after generation succeeds — including the empty-retrieval short-circuit case. Streaming remains deferred (not abandoned). No new dependency.
- **`GET /conversations/{id}` reads back a conversation's full history (ERP-019)**: the read-side companion to ERP-018, on a new top-level `conversations_router` (prefix `/conversations`, registered in `app/main.py` alongside the existing `/generation` router). `app/generation/repository.py` gained `get_conversation` (plain lookup, no create-on-miss, unlike `get_or_create_conversation`) and `get_all_messages` (unlimited, oldest-first, unlike the windowed `get_recent_messages`); `app/generation/service.py`'s new `get_conversation_history` returns `None` for an unknown ID, which the router maps to `404` (a GET does not implicitly create a conversation the way `POST /generation/query` does). Returns full history, no pagination. New `Message`/`ConversationHistoryResponse` schemas. No new dependency. Merged to `develop` via PR #14 (merge commit `50628a2`); not yet promoted to `main`.
- **`POST /generation/query/stream` streams a grounded, cited answer as Server-Sent Events (ERP-020)**: a new endpoint on the existing `/generation` router (`app/generation/router.py`), alongside `POST /generation/query` which remains byte-for-byte unchanged. `LLMClient` (`app/generation/client.py`) gained `generate_stream`, backed by Ollama's `chat(..., stream=True)`; `app/generation/service.py` gained `generate_stream(...)`, mirroring `generate()`'s stateless/stateful branching (history load, rewrite, retrieval, empty-context short-circuit — full ERP-018 conversation-memory parity from the start) but yielding `(event, data)` tuples instead of returning one response: `citations` once (before any answer text, since it's known from retrieval before generation starts), `token` per chunk of generated text, then a terminal `done` or `error`. Persistence (both turns, one transaction) happens only after the full answer is assembled, immediately before the `done` event — a client disconnect (`GeneratorExit` at the suspended `yield`) or a mid-generation exception both skip it naturally, matching `generate()`'s all-or-nothing semantics exactly. Because SSE headers (200 OK) are sent as soon as streaming starts, a mid-stream failure can't become an HTTP error status the way `POST /generation/query`'s `HTTPException(503)` does — it surfaces as a terminal `error` SSE event instead. No new dependency. Closes out `docs/roadmap.md`'s Streaming Responses item, named in prior `current-state.md` entries as the largest remaining capability gap. Design spec: `docs/superpowers/specs/2026-09-03-streaming-generation-design.md`. Also carries SSE anti-buffering headers (`Cache-Control: no-cache`, `X-Accel-Buffering: no`) added during final review, so the stream doesn't silently buffer behind a default-configured reverse proxy. Merged to `develop` via PR #16 (merge commit `f488972`), then promoted to `main` via PR #17 (merge commit `b08202b`).
- **A Redis cache-aside layer sits in front of `app/retrieval/service.py`'s `search()` (ERP-021)**: `app/retrieval/config.py` gained `RetrievalSettings` (`RETRIEVAL_`-prefixed `redis_url`, `cache_ttl_seconds` defaulting to 300s), and a new `app/retrieval/cache.py` (`RetrievalCache` protocol + `RedisRetrievalCache`) mirrors ERP-013's embedding-cache resilience pattern — a Redis outage degrades to always-miss, logged rather than raised. `search()`'s cache key hashes all four parameters that affect its output (`query`, `top_k`, `rerank`, `expand_sections`); a hit returns immediately, skipping the full pipeline (query embedding, FAISS search, Postgres BM25 search, RRF fusion, hydration, optional rerank/section-expansion); a miss — including an empty-results miss — runs the pipeline and caches the result before returning. `search()` gains an injectable `cache` parameter defaulting to `RedisRetrievalCache`, following the same injectable-dependency shape as `embedding_client`/`faiss_index`/`reranker`. Both existing callers (`app/retrieval/router.py`'s `POST /retrieval/query` and `app/generation/service.py`'s internal use of `search()`) get caching transparently, with no changes to either module. No new infra — reuses the `redis:7-alpine` service already in `docker-compose.yml`/CI from ERP-013. This is the second of ADR-003's three named Redis cache-aside uses (after ERP-013's embedding cache); the third (session/auth-token cache) is now built in ERP-026.
- **`app/embedding/cache.py`'s `RedisEmbeddingCache` is hardened the same way ERP-021 hardened `RedisRetrievalCache` (ERP-022)**: `EmbeddingSettings` gained `redis_socket_timeout_seconds` (default `2.0`), passed into `redis.Redis.from_url(...)` as `socket_connect_timeout`/`socket_timeout`; `get()`'s `json.loads` deserialization is now wrapped in `try/except (ValueError, UnicodeDecodeError)`, degrading to a logged cache miss instead of raising on a corrupt value; a new `lru_cache`-wrapped `get_default_embedding_cache()` factory replaces `OllamaEmbeddingClient`'s previous per-instance inline `RedisEmbeddingCache(settings)` construction, so the process reuses one Redis client/connection pool. Cache-key scoping by user/tenant is now implemented in ERP-026, but only for ERP-021's retrieval cache (`app/retrieval/service.py`'s `_cache_key` gained an `owner_id`-inclusive key in Task 13) -- this embedding cache was deliberately left unscoped: an embedding vector for a given text is the same regardless of who requested it (embeddings aren't user-specific data the way retrieval results are), so scoping it by owner would only fragment the cache without closing any leak. Verified: `ruff`/`mypy` clean, `pytest tests/embedding` (26 passed), full suite with real Postgres+Redis (189 passed, 99.31% coverage). Merged to `develop` via PR #19, then promoted to `main` via PR #20 (2026-09-04).
- **`search_vector`'s GIN index is declared on the ORM model, and its underlying migration strategy is now non-locking (ERP-024, ERP-025)**: `app/ingestion/models.py`'s `ChunkRecord` gained `Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin")` in `__table_args__` (ERP-024), so `Base.metadata.create_all` (the test suite's schema bootstrap) creates the same index production gets via Alembic — confirmed via an empty `alembic revision --autogenerate` diff. ERP-025 then converted `search_vector` from a `GENERATED ALWAYS`/`STORED` column to a plain nullable `TSVECTOR` kept in sync by a Postgres trigger (`chunks_search_vector_update()` / `chunks_search_vector_trigger`, `BEFORE INSERT OR UPDATE OF text`), via new migration `ec9863a88014`: drops the old index, `ALTER TABLE ... DROP EXPRESSION` (in-place, no table rewrite), creates the trigger, runs a batched backfill loop (a no-op today, general-purpose for future scale), then rebuilds the GIN index with `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()`. The model mirrors this via `event.listen(ChunkRecord.__table__, "after_create", DDL(...))` so tests get identical auto-populate behavior without running Alembic. Downgrade restores ERP-014's original generated-column schema exactly; both directions verified against a real Postgres DB, plus an empty autogenerate diff after upgrading. No behavior/contract change — `search_chunks_by_text` and all hybrid-retrieval tests pass unmodified. Verified: ruff/mypy clean, full suite 189 passed, 99.32% coverage. Merged to `develop` via PR #21 (merge commit `30fb981`, 2026-09-05), then promoted to `main` via PR #23 (merge commit `1887b34`, 2026-09-05).
- **`main`'s branch protection now requires the CI `test` status check to pass before merging (ERP-023)**: `required_status_checks: {strict: true, contexts: ["test"]}`, with existing settings (`required_pull_request_reviews`, `allow_force_pushes: false`, `allow_deletions: false`) preserved unchanged. Claude's own `gh api -X PUT .../branches/main/protection` call was denied by the environment's auto-mode permission classifier on three attempts; the user ran the equivalent command directly from their own terminal instead, which succeeded (see `.ai/sessions/2026-09-05-search-vector-index-and-nonlocking-migration.md` for the exact command and the `-F "restrictions=null"` fix needed to get past a GitHub-side schema-validation 422 on the first attempt).
- **Local email/password authentication with role-based per-user data isolation (ERP-026)**: a new `app/auth/` module (`models.py`, `schemas.py`, `service.py`, `router.py`, `security.py`, `dependencies.py`) implements `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout` with JWT access tokens + rotating opaque refresh tokens. New `users` and `refresh_tokens` tables via Alembic migration; `documents` and `conversations` gain required `owner_id` columns, backfilled to a fixed `system` user for pre-existing rows. Every existing endpoint (`ingestion`, `retrieval`, `generation`, `conversations`) requires a valid access token via `get_current_user`; retrieval, ingestion job status, and conversation history are scoped to the caller's own data (wrong-owner access returns 404). Refresh-token rotation-on-use with a Redis-backed revocation cache (cache-aside, never load-bearing) completes ADR-003's third named Redis use. Built via subagent-driven development (16 tasks, each independently reviewed) plus a final whole-branch review. A per-task security review (Task 14) caught a Critical bug in `get_or_create_conversation`/`get_recent_messages` — missing ownership checks on client-supplied `conversation_id` allowing cross-user read/write — fixed in commit `c51e17e`. The final whole-branch review then caught and fixed (commit `77fb7ee`): a retrieval filter-ordering bug where FAISS's owner-blind candidates could consume a caller's `top_k` result slots before the owner filter ran (fixed by filtering candidate vector IDs to the caller's own documents before RRF fusion, not after); an uncaught exception when authenticating against the seeded `system` user (also a user-enumeration oracle, 500 vs 401); an undocumented required `AUTH_JWT_SECRET_KEY` env var that CI only satisfied by test-collection-order accident (now in `.env.example` and CI's `env:` block); and missing regression tests (401-without-token across all 6 protected routes, plus the 2 of 3 wrong-owner-404 paths that lacked cross-owner tests). Verified: 240 tests passing, 99%+ coverage, ruff/mypy/pre-commit all clean. Merged to `develop` via PR #22 (merge commit `39c8df7`, 2026-09-05), then promoted to `main` via PR #23 (merge commit `1887b34`, 2026-09-05).
- **Admin user-management endpoints exist (ERP-027)**: `GET /admin/users`, `PATCH /admin/users/{user_id}`, `POST /admin/users/{user_id}/revoke-sessions`, all gated by the `require_role("admin")` dependency ERP-026 had already scaffolded unused. `UserRecord` gained `is_active: bool` (default `True`) via a new Alembic migration (`cc12bb2f6bc7`, head; empty autogenerate diff confirmed). `login()`/`refresh_access_token()` now raise `AccountDisabledError` (mapped to `403`) for a disabled account, blocking new logins and refresh-token rotation immediately — but an already-issued access token is not checked per-request and rides out its own ≤30 min natural expiry (a deliberate trade-off chosen over adding a DB/cache check to the `get_current_user` hot path). Revoking a user's sessions is a DB-only bulk `UPDATE` on `refresh_tokens`, no per-token Redis cache writes. Verified: ruff/mypy clean, full suite 262 passed, 98.97% coverage, pre-commit clean. Merged to `develop` via PR #24 (merge commit `b4d9506`, 2026-09-05), then promoted to `main` via PR #27 (merge commit `d00ed98`, 2026-09-06). (Note: `gh pr merge` was blocked by the environment's auto-mode permission classifier, same as the ERP-023 branch-protection call — the user merged it directly, for this and every subsequent PR in this project.)
- **Distributed tracing and metrics exist across the RAG pipeline (ERP-028)**: OpenTelemetry is the single instrumentation layer (`app/core/telemetry.py`'s `configure_telemetry()`), auto-instrumenting FastAPI/SQLAlchemy/Redis/httpx and adding hand-written spans at the RAG-specific pipeline stages auto-instrumentation can't see (`embedding.generate`, `faiss.search`, `bm25.search`, `retrieval.fuse`/`retrieval.rerank`/`retrieval.expand_sections`, `llm.generate`). New metrics: `embedding_cache_requests_total`/`retrieval_cache_requests_total` (hit/miss, in the two existing Redis caches), `llm_generation_duration_seconds`, `ingestion_jobs_total` (done/failed). Traces export via OTLP to a new `docker-compose` Jaeger service; metrics are scraped by a new Prometheus service (`/metrics`, mounted via `prometheus_client.make_asgi_app()`); a new Grafana service is auto-provisioned with both datasources and a 6-panel dashboard. `app/core/logging_config.py` injects the active span's trace ID into every log record for log-to-trace pivoting. Same never-load-bearing philosophy as the Redis caches (ADR-003): `configure_telemetry()` swallows its own setup exceptions; an unreachable OTLP endpoint doesn't block startup or requests. All new tests use OTel's in-memory fixtures (`InMemorySpanExporter`/`InMemoryMetricReader`) — no real Jaeger/Prometheus dependency for CI, though the full stack was also verified end-to-end against real local containers (Prometheus scrape target confirmed `up`, Grafana dashboard confirmed provisioned via its API). Closes the "Observability" item from `docs/architecture.md`'s Project Goals. Verified: ruff/mypy clean, full suite 276 passed, 99.05% coverage. Design spec: `docs/superpowers/specs/2026-09-06-observability-design.md`; plan: `docs/superpowers/plans/2026-09-06-observability.md`. Merged to `develop` via PR #25 (merge commit `a3cde70`, 2026-09-06), then promoted to `main` via PR #27 (merge commit `d00ed98`, 2026-09-06).
- **A standalone retrieval-quality evaluation harness exists (ERP-029)**: `app/evaluation/` — a hand-authored golden dataset (`dataset.py`: 2 documents, 5 chunks, 4 queries, authored as `Chunk`-shaped templates rather than real PDF fixtures, since `document_id`/`chunk_id` are only stable once stamped by a run) is run through the real `embed_and_persist` + `retrieval.service.search()` pipeline by `runner.py`'s `run_evaluation()`, scored with Precision@k/Recall@k/MRR (`metrics.py`), and persisted to a new `evaluation_runs` Postgres table (`models.py`/`repository.py`). Invoked via `uv run python -m app.evaluation.run` (`run.py`), not a new API endpoint. Always builds its own temp-file-backed FAISS index (never the real app's persisted index) and deletes every other row it creates (eval user, documents, chunks) after each run — only the summary row persists. Not wired into CI as a gate yet (no established acceptable-threshold baseline); `tests/evaluation/` runs in CI using a fake embedding client, never real Ollama (consistent with the rest of this repo). Verified end-to-end against live Ollama and real Postgres (Precision@3=0.333, Recall@3=1.0, MRR=1.0; confirmed zero stray rows left behind). This is the retrieval half of the "Evaluation" project goal; generation-quality (LLM-as-judge) evaluation is deferred as a follow-up. Verified: ruff/mypy clean, full suite 291 passed, 98.21% coverage. Design spec: `docs/superpowers/specs/2026-09-06-evaluation-design.md`; plan: `docs/superpowers/plans/2026-09-06-evaluation.md`. Merged to `develop` via PR #26 (merge commit `229cf1e`, 2026-09-06), then promoted to `main` via PR #27 (merge commit `d00ed98`, 2026-09-06); `develop` and `main` were in sync as of that merge.
- **Design constraint recorded for the future "LLMOps & Evaluation Platform"** (in `docs/architecture.md`, 2026-09-06, before that repo exists): it must be a generalized, standalone platform other projects (including this one and the future Agentic AI project) can integrate with as clients, not something coupled to this codebase — this repo's own eval work (ERP-029) is understood to likely be replaced/integrated with it later, not the other way around.
- **Generation-quality evaluation exists, completing the "Evaluation" project goal (ERP-030)**: `app/evaluation/judges.py`'s `GenerationJudge` interface has two implementations — `RagasJudge` (default: Ragas's Faithfulness/Answer-Relevancy/Context-Precision metrics against a local-Ollama-backed judge LLM) and `OllamaLLMClientJudge` (selectable fallback: hand-rolled scoring prompts against the existing `OllamaLLMClient`). `app/evaluation/generation_runner.py`'s `run_generation_evaluation()` reuses ERP-029's golden dataset unchanged and its ingest/cleanup lifecycle, running each query through `search()` → `build_prompt()` → `llm_client.generate()` directly (not `app.generation.service.generate()`, whose public return lacks chunk text) to get the answer and its actual context, then scores and persists to a new `generation_evaluation_runs` table. CLI: `uv run python -m app.evaluation.generation_run [--judge ragas|ollama]`. **Critical**: `ragas` is pinned to `==0.3.9` and `[tool.uv] constraint-dependencies` includes `"langchain-community<0.4"` — both `ragas==0.4.3` and `0.3.9` otherwise fail to import at all (a confirmed upstream ragas bug importing a `langchain_community` submodule removed in `langchain-community>=0.4`, unrelated to LLM backend choice; see `.ai/tickets/ERP-030.md` for the exact GitHub issue numbers before ever bumping this dependency). Verified end-to-end against live Ollama: `--judge ollama` completed in seconds with plausible scores and clean cleanup; `--judge ragas` hung 10+ minutes with no output before being killed, confirming Ragas's documented local-Ollama reliability issue in practice — exactly why the fallback judge exists. Verified: ruff/mypy clean, full suite 298 passed, 97.06% coverage. Design spec: `docs/superpowers/specs/2026-09-06-generation-evaluation-design.md`; plan: `docs/superpowers/plans/2026-09-06-generation-evaluation.md`. Merged to `develop` via PR #28 (merge commit `12841cd`, 2026-09-06), then promoted to `main` via PR #29 (merge commit `49f8dc5`, 2026-09-06); `develop` and `main` were in sync as of that merge.
- **The vector index is now physically partitioned per owner instead of one shared FAISS index (ERP-031)**: `app/embedding/index.py`'s new `OwnerFaissIndexStore` lazily creates/loads one on-disk `FaissIndex` per `owner_id` (`<index_dir>/<owner_id>.bin`, `EmbeddingSettings.faiss_index_dir`, renamed from `faiss_index_path`), with a per-owner `threading.Lock` for concurrent-ingestion safety. `embed_and_persist` (`app/embedding/service.py`) and `app/retrieval/service.py`'s `search()` both take an injectable `faiss_index_store` (replacing `faiss_index`) instead of talking to `FaissIndex` directly. This replaces ERP-026's oversample-then-filter isolation mechanism (`filter_vector_ids_by_owner`, now deleted) with a structural guarantee: a caller's search physically cannot return another owner's vectors, since another owner's vectors are never in the file being searched -- closing the correctness-fragility gap named in ERP-026's own final-review notes and in this file's prior "Next Planned Work" entry. `RRF_OVERSAMPLE_MULTIPLIER` oversampling is kept, now purely for RRF fusion quality, not isolation. A one-off migration script (`app/embedding/migrate_to_per_owner.py`, `uv run python -m app.embedding.migrate_to_per_owner`) redistributes any pre-ERP-031 single-shared-index data into per-owner indexes by reconstructing each vector from the legacy index (requires `FaissIndex`'s underlying type to be `IndexIDMap2`, not the previously-used plain `IndexIDMap`, which cannot reconstruct); it is not an Alembic migration since it touches only FAISS data files, not Postgres schema. Design supports future sharding without a rewrite (every operation is keyed by `owner_id`, not tied to one shared filesystem). Design spec: `docs/superpowers/specs/2026-09-06-per-tenant-faiss-index-design.md`. Verified: ruff/mypy clean, full suite 307 passed, 96% coverage (against isolated Postgres/Redis containers -- this sandbox's default ports were occupied by a concurrent agent's containers). Live-Ollama re-verification of the ERP-029 evaluation harness (Precision@k/Recall@k/MRR) was not possible in this sandboxed environment (no local Ollama reachable) and is a deferred follow-up. Merged to `develop` via PR #31.
- **OIDC login exists as an additional authentication path alongside local email/password (ERP-032)**: `GET /auth/oidc/{provider}/login` (307 redirect to the IdP) and `GET /auth/oidc/{provider}/callback` (200 JSON, same `access_token`/`refresh_token` pair `POST /auth/login` issues) on a new `oidc_router`; `POST /auth/login`/`register`/`refresh`/`logout` are byte-for-byte unchanged. Provider-agnostic Authorization Code + PKCE client (new `app/auth/oidc.py`, using Authlib's `joserfc` for JWKS/ID-token verification) — every provider detail comes from the issuer's `.well-known/openid-configuration` discovery document, resolved at request time from `AuthSettings`'s new `oidc_*` fields; no Google-specific code, though Google is the reference/test provider. New `oidc_identities` table (unique on `(provider, external_id)`, migration `dd26f4e8c54f`) rather than columns on `users`, so one user can hold a local password and linked OIDC identities simultaneously; `users.hashed_password` is now nullable. **Account linking**: an OIDC login whose email matches an existing user auto-links only when the ID token asserts `email_verified: true`; otherwise `409 Conflict`, nothing created — this is what blocks an attacker with an unverified claim to someone else's email from taking over that account. A pre-merge security self-review caught and fixed a real flaw in an earlier draft: putting the PKCE verifier/nonce inside the URL-visible `state` parameter (no server session) both defeated PKCE (verifier and code shared one URL-visible channel) and left no login-CSRF defense; fixed with a short-lived `HttpOnly`/`Secure`/`SameSite=Lax` cookie carrying the verifier/nonce, with `state` reduced to a plain anti-CSRF token checked against the cookie. New dependencies: `authlib` (pre-approved) and `httpx` (promoted from transitive-dev to direct, since `app/auth/oidc.py` imports it). Tests never hit a real IdP -- `httpx.MockTransport` fakes discovery/JWKS/token-exchange, and ID-token verification runs against a real in-test RSA keypair. Verified: ruff/mypy clean, full suite 347 passed with coverage above the 90% gate. Only Google is a concrete verified reference provider; no self-service identity-linking flow for an already-logged-in user. Design spec: `docs/superpowers/specs/2026-09-06-oidc-authentication-design.md`. Merged to `develop` via PR #32 (merge commit `c15659e`, 2026-09-07).
- PR #1 (`develop` → `main`) merged 2026-08-01. PR #2 (`develop` → `main`, secrets-scanning guardrail + ruff/mypy tooling) merged 2026-08-02. ERP-006 (pytest coverage gate + logging convention, see item above) merged `develop` → `main` 2026-08-02. ERP-011 merged to `develop` via PR #4 (merge commit `a019e35`). ERP-012 merged to `develop` via PR #5 (merge commit `2792e90`). PR #6 (`develop` → `main`, promoting both ERP-011 and ERP-012) merged 2026-08-26 (merge commit `6471a4f`). ERP-013 merged to `develop` via PR #7 (merge commit `c3d90ff`). ERP-014 merged via PR #8 (merge commit `933dfdb`), ERP-015 via PR #9 (merge commit `f7c3019`), ERP-016 via PR #10 (merge commit `ae69a5a`) — all 2026-08-30. PR #11 (`develop` → `main`, promoting ERP-013 through ERP-016) merged 2026-08-31 (merge commit `8c5fe90`); `develop` and `main` were in sync as of that merge. ERP-017 merged to `develop` via PR #12, ERP-018 via PR #13, and ERP-019 via PR #14 (merge commit `50628a2`). PR #15 (`develop` → `main`, promoting ERP-017 through ERP-019) merged 2026-09-02 (merge commit `2f1cba3`); `develop` and `main` were in sync as of that merge. ERP-020 merged to `develop` via PR #16 (merge commit `f488972`). PR #17 (`develop` → `main`, promoting ERP-020) merged 2026-09-03 (merge commit `b08202b`); `develop` and `main` were in sync as of that merge. ERP-021 merged to `develop` via PR #18 (merge commit `476e65c`). ERP-022 merged to `develop` via PR #19 (2026-09-04). PR #20 (`develop` → `main`, promoting ERP-021 and ERP-022) merged 2026-09-04 (merge commit `c5e90b7`); `develop` and `main` were in sync as of that merge. ERP-024/ERP-025 merged to `develop` via PR #21 (merge commit `30fb981`, 2026-09-05). ERP-026 merged to `develop` via PR #22 (merge commit `39c8df7`, 2026-09-05). PR #23 (`develop` → `main`, promoting ERP-024/ERP-025 and ERP-026) merged 2026-09-05 (merge commit `1887b34`); `develop` and `main` were in sync as of that merge. ERP-027 merged to `develop` via PR #24 (merge commit `b4d9506`, 2026-09-05). ERP-028 merged to `develop` via PR #25 (merge commit `a3cde70`, 2026-09-06). ERP-029 merged to `develop` via PR #26 (merge commit `229cf1e`, 2026-09-06). PR #27 (`develop` → `main`, promoting ERP-027 through ERP-029) merged 2026-09-06 (merge commit `d00ed98`); `develop` and `main` were in sync as of that merge. ERP-030 merged to `develop` via PR #28 (merge commit `12841cd`, 2026-09-06). PR #29 (`develop` → `main`, promoting ERP-030) merged 2026-09-06 (merge commit `49f8dc5`); `develop` and `main` were in sync as of that merge.

- **Live deployment-readiness testing against real local Ollama models (2026-09-08), and cross-project infrastructure research**: ran the app against real installed Ollama models (`nomic-embed-text`, `gemma3:4b`, `qwen3:8b`) for the first time -- not just unit tests with fakes -- to answer "does this actually work when made live". Found and fixed three real bugs a green CI suite had missed entirely: `GenerationSettings.model`'s default `"qwen3"` didn't resolve on a real Ollama install since Ollama only resolves untagged names to an implicit `:latest`, not whatever tag is actually installed -- fixed via new `app/core/model_check.py`/`app/core/check_models.py` (`uv run python -m app.core.check_models` fails fast with an actionable message if a configured model tag isn't installed) plus explicit INFO-level logging at client construction and `.env.example` documentation (ERP-034, Done); no documented hardware/VRAM sizing guidance, confirmed live via two different OOM failure modes on a 6GB-VRAM/16GB-RAM laptop under concurrent-agent memory pressure, resolved once RAM pressure eased -- not a hardware defect -- now written up in new `docs/deployment.md` (ERP-035, Done); `OllamaLLMClientJudge` (ERP-030) silently scored an unparseable judge response as `0.0`, indistinguishable from a genuinely bad answer -- fixed via a bounded one-time retry plus a distinct `None` sentinel (not `0.0`) when still unparseable, with `GenerationEvaluationSummary` gaining separate `*_parse_failures` counts so means are computed only over parseable scores (ERP-036, Done). The same live run also gave positive evidence the pipeline itself works: `gemma3:4b` scored 1.00 faithfulness on all 4 golden-dataset queries -- grounded, correctly-cited, non-hallucinated answers. Separately, researched hosting/compute options (live web search, dated 2026-09-08) for making this platform reachable for a bounded 2-3 month/5-20-user test window, consolidated into a new shared cross-project file `D:\github-projects\infrastructure-options.md` (ADR-008) since the same constraint applies to the future Agentic AI/PEFT/LLMOps repos -- current leading choice is GCP's permanently-free `e2-micro` VM for app+FAISS hosting (pending live verification), Render Starter as fallback, Neon/Upstash/Modal already decided. ERP-037 tracks the live-verification-and-execution pass against that choice, and now has no unmet dependency. Verified (ERP-034/035/036 fixes): ruff/mypy clean, full suite 367 passed, 97% coverage (real Postgres+Redis). Session logs: `.ai/sessions/2026-09-08-deployment-readiness-and-infra-research.md`, `.ai/sessions/2026-09-08-fix-model-tag-and-eval-judge-bugs.md`.
- **Renamed `enterprise-rag-platform` to `self-hosted-rag-platform` (2026-09-08)**: the "enterprise" framing overpromised relative to what's built; "self-hosted" (Ollama, no cloud LLM dependency) is accurate. Renamed the Python package (`pyproject.toml`, regenerated `uv.lock`), FastAPI app title, Prometheus job name, Grafana dashboard title, and the GitHub repo itself (`bankar-ai/self-hosted-rag-platform` -- `gh repo rename` also auto-updated the local `origin` remote URL). The local clone's folder path (`D:\github-projects\enterprise-rag-platform`) was deliberately left unchanged to avoid disturbing active git worktrees and the current session. Also renamed the future "Agentic Insurance Assistant" project to "Agentic AI" in `docs/architecture.md` (insurance-domain framing was dropped in favor of a general-purpose single-agent learning project, scope still otherwise undecided).

- **The RAG platform is live-deployed for the 2-3 month test window (ERP-037, 2026-09-10)**: GCP `e2-micro` (`rag-platform-host`, `us-central1-a`) confirmed GO after live memory-headroom testing (not assumed) -- app runs directly via `uv run uvicorn` under systemd (`rag-platform.service`, `Restart=on-failure`, survives reboot), not Docker, since the VM's ~958MB RAM has no headroom for the Docker daemon's own overhead on top of the app. Caddy reverse-proxies real Let's Encrypt TLS via a free `sslip.io` hostname (no purchased domain): `https://34-31-5-88.sslip.io`. **A real bug found and fixed via this live deployment, not caught by any test suite**: `docling` (an ingestion *fallback* path, rarely reached) was imported eagerly at module level in `app/ingestion/parsers.py`, forcing every process start to pay its `torch`/`transformers` import cost (~500MB+ resident) regardless of whether the fallback is ever used -- made lazy (moved inside `parse_quality()`), roughly halving baseline memory (509MB -> 268MB RSS), which is what made `e2-micro` viable at all. Neon (Postgres) and Upstash (Redis) both provisioned and verified with real traffic (not just connectivity checks): `POST /auth/register` returned `201` with a real persisted row; `POST /auth/login` -> `POST /auth/refresh` both `200`, exercising the Redis-backed revocation cache for real -- note there is no single `REDIS_URL`, three separate settings classes each need their own prefixed var (`EMBEDDING_REDIS_URL`, `RETRIEVAL_REDIS_URL`, `AUTH_REDIS_URL`), all pointing at the same Upstash instance. Modal: new `deploy/modal_ollama.py` serves Ollama (`gemma3:4b` + `nomic-embed-text`, baked into the image at build time) as a public HTTPS endpoint speaking Ollama's native API directly -- zero app-code changes needed, just pointing `GENERATION_OLLAMA_HOST`/`EMBEDDING_OLLAMA_HOST` at it. Two real deployment bugs found and fixed along the way: a reference tutorial's hardcoded Ollama download URL was stale (Ollama repackaged `.tgz` -> `.tar.zst` since), and Ollama binds `127.0.0.1` by default, invisible to Modal's reverse proxy, requiring `OLLAMA_HOST=0.0.0.0:11434` set explicitly. **Full pipeline verified end-to-end on the live deployment**: a real chunk ingested, retrieved, and answered correctly and grounded via the live Modal endpoint -- "The Eiffel Tower is located in Paris, France [1]." **Concurrent-load smoke test (2026-09-10) closes out ERP-037**: 15 concurrent requests against the live public endpoint (10x `GET /docs` + 5x full register->login->generate flows) -- 15/15 succeeded, no errors. `docs` calls ~1.3s (confirms the VM/Caddy/uvicorn layer handles concurrency fine); the 5 generation flows clustered at 52-59s each -- Modal spins up a separate cold container replica per concurrent request rather than queuing behind one warm instance (query embedding alone hits Modal even on the empty-retrieval short-circuit). Correctness holds under concurrency; this is a latency characteristic of scale-to-zero under a genuine concurrent burst, not a failure -- deliberately not mitigated (would need `min_containers=1` on `deploy/modal_ollama.py`, trading away some cost savings) since truly simultaneous first-touch traffic is unlikely at 5-20 sporadic users. **ERP-037 is Done** -- all 9 acceptance criteria met. All infra details, account identifiers, how-to-check/how-to-recover, and teardown steps for all four services (GCP/Neon/Upstash/Modal) tracked in `D:\github-projects\gcp-deployment-tracker.md` (outside this repo's git history, per ADR-008's pattern).

- **Live deployment's traces now export to Grafana Cloud's free tier (ERP-038, 2026-09-15)**: the
  local `docker-compose` Jaeger/Prometheus/Grafana stack from ERP-028 has nowhere to run on the
  live `e2-micro` VM (no RAM headroom), so ERP-028's existing OTel instrumentation had nowhere to
  send traces once deployed (ERP-037). `app/core/telemetry.py`'s new `_build_span_exporter()` picks
  the OTLP exporter class from the standard `OTEL_EXPORTER_OTLP_PROTOCOL` env var --
  `"grpc"` (default, unset) unchanged for local Jaeger on port 4317; `"http/protobuf"` for Grafana
  Cloud's OTLP gateway, which is a path-based HTTPS URL, not a gRPC host:port. Both exporter classes
  come from the `opentelemetry-exporter-otlp` meta-dependency already in `pyproject.toml` -- no new
  dependency. The VM's `~/app/.env` now carries `OTEL_EXPORTER_OTLP_ENDPOINT`/`_PROTOCOL`/`_HEADERS`
  (Basic auth, base64(instance_id:api_token), with the Python-specific `Basic%20` space-encoding
  quirk) plus `OTEL_SERVICE_NAME=self-hosted-rag-platform` (added after traces initially showed up
  as `unknown_service`). Verified end-to-end with real traffic, not just config: a real
  `POST /retrieval/query` against the live deployment produced a trace in Grafana Cloud's
  Explore -> Tempo view rooted at `self-hosted-rag-platform POST /retrieval/query`, with
  `embedding.generate`/`faiss.search`/`bm25.search`/`retrieval.fuse` correctly nested underneath.
  Metrics and application logs are explicitly deferred (see ERP-039). Docs: `docs/deployment.md`'s
  new "Traces (Grafana Cloud)" section; non-secret operational details in
  `D:\github-projects\gcp-deployment-tracker.md`; the API token in
  `C:\Users\Pankaj\.credentials\self-hosted-rag-platform-credentials.md`. Verified: ruff/mypy clean,
  full suite 369 passed (real Postgres/Redis/Jaeger). Session log:
  `.ai/sessions/2026-09-15-grafana-cloud-traces.md`.

- **Live deployment's application logs now also export to Grafana Cloud (ERP-039, 2026-09-15)**:
  extends ERP-038's stack to logs, in-process -- `configure_logging()`
  (`app/core/logging_config.py`) attaches an OTLP `LoggingHandler` alongside the existing stdout
  handler, via a new `_build_log_exporter()` mirroring `app.core.telemetry`'s protocol-selection
  pattern (same `OTEL_EXPORTER_OTLP_*` env vars, no new ones, no new Grafana Cloud token -- the
  existing token's scope already covers logs). Chose in-process over a log-shipping agent
  (Alloy/Promtail) based on **measured** evidence, not assumption, per the ticket's explicit
  requirement: uvicorn RSS and system-wide available memory were unchanged before vs after
  deploying (~267-274MB RSS, ~365-378MB available both times) -- a second export path on an
  already-running process costs effectively nothing, whereas a separate agent would compete for the
  same tight budget that required ERP-037's lazy-docling-import fix just to fit the app alone.
  Verified end-to-end with real traffic: a live `POST /retrieval/query` produced log lines in
  Grafana Cloud's Loki (`grafanacloud-microstarfish1843-logs`), each carrying `trace_id`/`span_id`
  fields plus a "Links -> traceID" affordance pivoting straight to the matching trace in Tempo --
  confirming `TraceIdFilter`'s log-to-trace correlation design now works against the live backend,
  not just local Jaeger. Verified: ruff/mypy clean, full suite 372 passed, 96.59% coverage (100% on
  `logging_config.py` itself). Docs: `docs/deployment.md`'s new "Application logs" section;
  `D:\github-projects\gcp-deployment-tracker.md` updated. This closes out both halves of the
  observability-for-the-live-deployment gap named after ERP-037 (ERP-038 traces + ERP-039 logs).
- **A saved Grafana Cloud dashboard ("AI Platforms -- Service Observability") now exists for demo/showcase use (2026-09-16)**, built to be reusable across future projects (Agentic AI, PEFT, LLMOps) via a `service_name` picker, not hardcoded to this repo. It uses **static options**, not live label-value auto-discovery -- Loki's `label_values()` variable query returned no results on this stack despite `service_name` being a real, directly-queryable label (confirmed working in raw LogQL queries); root cause untriaged, worked around by manually listing each service as a static option instead. Panels: log volume by level, recent traces (Tempo), errors-only log stream, all recent logs (Loki). See `D:\github-projects\gcp-deployment-tracker.md`'s "Observability" section for the exact how-to-extend steps. Considered and explicitly declined: Neon's native Grafana Cloud integration (requires the paid Scale plan, no free tier support -- would break this deployment's deliberate all-free-tier design) and a GCP Cloud Monitoring data source for VM infra metrics (skipped as unnecessary scope for now, app-level observability already covers demo needs). Two more throwaway verification test users were created and left in the live database during this session (`erp039-verify@example.com`, `dashboard-verify@example.com`), same known gap as ERP-038/039's own test users -- no delete-user endpoint exists yet -- closed by ERP-040 below.
- **`DELETE /admin/users/{user_id}` (ERP-040, 2026-09-16)**: a genuine hard-delete admin endpoint, closing the cleanup gap the ERP-037/038/039/dashboard sessions kept running into. `app/auth/repository.py`'s new `delete_user_and_owned_data` deletes every row a user owns (conversation messages/conversations, chunks/documents, refresh tokens, OIDC identities) in FK-safe order before the user row itself, mirroring the existing `app.evaluation.repository.cleanup_eval_data` pattern; `app/auth/service.py`'s `delete_user` also best-effort deletes the user's per-owner FAISS index file (ERP-031) and blocks an admin from deleting their own account (409). **Live-verified**: the live deployment had no bootstrapped admin at all until this session -- created a real first admin (`ops-admin@self-hosted-rag-platform.internal`, credentials in the credentials store) directly via the repository layer, distinct from the pre-existing migration-seeded `system@internal` account (intentionally-invalid password hash, can't log in). Used it to delete all three throwaway test users left by ERP-038/039/the dashboard build; `GET /admin/users` confirmed only the two legitimate admins remain. Verified: ruff/mypy clean, full suite 381 passed, 96.66% coverage.

- **ERP-041 re-verified retrieval-quality evaluation post-ERP-031 (2026-09-16)**: ran `uv run python -m app.evaluation.run` against real local Ollama and Postgres/Redis. Result: Precision@3=0.333, Recall@3=1.000, MRR=1.000 — an exact match to the ERP-029 baseline, confirming ERP-031's per-owner FAISS partitioning introduced no retrieval-quality regression. No code change needed. No PR (pure verification).
- **ERP-042 shipped metrics to Grafana Cloud, completing the observability trio (2026-09-16)**: `app/core/telemetry.py`'s `MeterProvider` now attaches a push-based OTLP `PeriodicExportingMetricReader` alongside the existing pull-based `PrometheusMetricReader` (local dev unaffected), mirroring `_build_span_exporter()`/`_build_log_exporter()`'s exact protocol-selection pattern — no new dependency, no new env vars, and the existing `set:alloy-data-write` token turned out to already cover metrics ingestion (no new Grafana Cloud token needed). Deployed to the live VM; live-verified via the Grafana Cloud Explore UI (Prometheus datasource `grafanacloud-microstarfish1843-prom`): real requests against the live app produced `http_server_duration_milliseconds_bucket`/`http_server_active_requests`/`db_client_connections_usage` series filtered by `service_name="self-hosted-rag-platform"`. VM memory re-verified post-deploy at 368Mi available — an exact match to the existing 365-378MB baseline, confirming the push-based exporter costs nothing measurable, same as ERP-039's finding for logs. Verified: ruff/mypy clean, full suite 383 passed, 96.68% coverage. Docs: `docs/deployment.md`'s new "Metrics (Grafana Cloud)" section; `D:\github-projects\gcp-deployment-tracker.md`'s Observability table gained a metrics row. Merged to `develop` via PR #34, promoted to `main` via PR #35 (both 2026-09-16); `develop` and `main` were in sync as of that merge.

## Next Planned Work

- **ERP-085 (mobile usability verification) found 4 real bugs on a real phone, not yet fixed
  (2026-09-21)**: live-verified `bankar-ai-self-hosted-rag-platform.vercel.app` on a real
  Android/Chrome device per its acceptance criteria. Found: (1) the top nav/header wraps and
  visually collides with the page title on narrow viewports; (2) reaching Documents/email/Log
  out requires horizontally scrolling the whole page — a direct violation of ERP-078's own "no
  horizontal scroll of the page body" criterion, since the header was apparently never covered
  by that responsive pass; (3) `SourcePanel`'s chunk text overflows horizontally instead of
  wrapping; (4) `SourcePanel`'s Copy/Close controls are off-screen by default as a result of
  (3) — a real tap-target reachability problem. The sidebar overlay itself (New chat, recent
  conversations, document checklist) was fully usable, no issues. Likely shared root cause:
  fixed-width/`nowrap` flex rows in the header and `SourcePanel` never given
  `flex-wrap`/`min-w-0`/`break-words`. Fix work deferred to next session (weekly usage quota
  ~80% used). Session log: `.ai/sessions/2026-09-21-mobile-usability-verification.md`; ticket:
  `.ai/tickets/ERP-085.md`.
- **A second follow-up batch (ERP-075 through ERP-081, plus ERP-083) built and committed to a
  branch, not yet merged/deployed (2026-09-19)**: closes out the remaining items from the punch
  list assembled after ERP-050/ERP-067-074 (ERP-082 OIDC self-service linking and DOCX/PPTX
  ingestion explicitly excluded, per user instruction). **ERP-075** — copy button for a full Q+A
  turn, and per-row copy on the sidebar's conversation list (fetched on demand). **ERP-081** —
  `SourcePanel` (react-markdown, ERP-067) is now lazy-loaded; main bundle 581KB → 287KB
  minified. **ERP-077** — a `[n]` citation marker inside `**bold**` text is now clickable (the
  bold-rendering branch previously never routed through the citation logic at all). **ERP-079**
  — `SourcePanel` is a real dialog now (`role="dialog"`, focus trap, Escape-to-close, focus
  returns to the triggering citation on close). **ERP-078** — sidebar and source panel both
  become overlays below the `md` breakpoint instead of a fixed three-column layout. **ERP-080**
  — `conversation_messages` gained a nullable `citations` JSONB column (migration
  `0e0c25ec1392`); reloaded conversation history now keeps working, clickable citation markers
  instead of losing them. **ERP-076** — the Cloud Run `docling-service`'s `/parse` response now
  also returns Docling's own document-level confidence grade (`mean_grade`); new
  `documents.parsing_confidence` column (migration `4c6edf78ba5c`, backfilled `"high"`),
  rendered as a badge next to each document in the Documents page and Chat sidebar — lets a
  caller tell at a glance whether a document (e.g. scanned/blurry) parsed reliably. **ERP-083**
  — code-complete but **not deployed**: a new isolated CI-only Modal app
  (`deploy/modal_ollama_ci.py`, deliberately separate from production after production's real
  2026-09-17 free-credit-threshold incident), `--fail-under-*` flags on both evaluation CLIs, and
  a new daily-scheduled `evaluation-gate.yml` workflow — but deploying the Modal app, attaching a
  payment method, and adding the `CI_MODAL_OLLAMA_URL` repo secret are left as manual follow-up
  (live/billable/credential actions), documented in `D:\github-projects\gcp-deployment-tracker.md`'s
  new "CI evaluation gate" section. Built in worktree `.claude/worktrees/erp075-083-followups`
  (branch `worktree-erp075-083-followups`, based on `develop`), one commit per ticket. Verified:
  ruff/mypy clean, backend 516 → 528 tests passing, frontend 55 → 60 tests passing,
  `tsc`/`oxlint`/`vite build` clean. **Not yet live-verified in a browser** (no browser-automation
  tool available this session) and **not yet merged or deployed** — see the session log
  (`.ai/sessions/2026-09-19-erp075-083-live-feedback-followups.md`) for full details and next
  steps.
- **A follow-up batch (ERP-067 through ERP-074) closed out live feedback from ERP-050's own
  first use (2026-09-19)**, deployed and live-verified: **ERP-074** — `Button` gained a
  `variant` prop, fixing invisible Delete/Retry/Dismiss text (a Tailwind cascade-order conflict
  between a hardcoded default color and per-call-site overrides). **ERP-067** — `SourcePanel`
  now renders chunk text via `react-markdown`/`rehype-raw`/`rehype-sanitize` instead of literal
  `**`/`#####`/`<mark>` syntax (bundle grew ~285KB→~580KB minified, flagged as a follow-up to
  lazy-load `SourcePanel`, not a blocker). **ERP-068** — copy-to-clipboard for a message, a
  conversation, and a source panel's text via one shared `CopyButton`. **ERP-069** — a confirmed
  live hallucination (a fabricated date not in the cited source) fixed by tightening
  `SYSTEM_PROMPT` to forbid stating any specific fact not verbatim present in context —
  **live-reproduced the exact failing conversation against production** before/after, confirming
  the fix. **ERP-070/ERP-073** — Documents-page uploads now stage behind an explicit "Upload"
  button instead of starting immediately, and a failed upload *transfer* shows a dismissible
  "Try again" state instead of silently vanishing. **ERP-071** — bulk delete + a
  `window.confirm` gate before any delete. **ERP-072** — new `DELETE /ingestion/jobs/{job_id}`
  actually cleans up a dismissed failed job's temp file (previously leaked disk space
  indefinitely) — **live-verified via SSH**: uploaded a file that fails ingestion, confirmed its
  temp file existed on the VM, dismissed it via the new endpoint, confirmed via a second SSH
  check the file and its parent directory were gone. Built via subagent-driven development (7
  tasks); one task (ERP-072's backend half) went through a fix round after task review caught an
  unauthorized retry-loop-plus-unrelated-function-edit deviation chasing a Windows-only test
  flake, reverted to the plan's simple code with the flake fixed at its actual root instead. The
  final whole-branch review then caught and fixed a real Critical bug before merge: the plan's
  own `handleRetry` wiring called the new backend-deleting dismiss function, which raced
  `retry_job`'s deliberate reuse of the same temp file — starting a retry could delete the file
  the retry itself needed, an unrecoverable data-loss bug in the pre-existing ERP-053 retry
  feature — fixed by splitting dismiss into a local-only (retry-safe) cleanup and the full
  backend-deleting version (explicit Dismiss only), plus two related Important fixes (a failed
  bulk-delete silently looking like it succeeded in the UI; a network error leaving delete rows
  stuck disabled forever). Backend 508→516 tests, frontend 36→55 tests (`DocumentsPage.test.tsx`
  is this page's first test file). Merged to `develop` via PR #50.
- **ERP-050 (Visual/UX Redesign) is Done, deployed, and live-verified (2026-09-19)**: a caller
  can now click any citation — the citation list below an answer, or (new) a clickable inline
  `[n]` marker in the answer text itself — to open a right-hand source panel showing the exact
  chunk text it came from, via a new owner-scoped `GET /documents/{document_id}/chunks/{chunk_id}`
  endpoint (chunk text fetched on demand, never embedded in `Citation`). The Chat page is now a
  three-pane layout (sidebar — extracted into its own `Sidebar` component — / chat / source
  detail, the third column appearing only when a citation is selected), plus a small cosmetic
  accent-color pass via a new Tailwind v4 `@theme` block. Deliberately out of scope: PDF
  storage/viewer (ingestion never persists original PDF bytes), sibling/section-expansion
  chunks in the panel, a NotebookLM-style "Studio" generated-artifacts pane. Built via
  subagent-driven development (8 tasks, each independently task-reviewed) plus a final
  whole-branch review (most capable model) that caught and fixed a real pre-merge bug: a
  stale-response race in `SourcePanel` where rapidly switching citations could silently show
  one citation's text under a different citation's header — fixed with a `cancelled`-flag guard
  and a moved-outside-the-fetch-switch metadata header, both verified by a scoped re-review.
  Backend 502 → 508 tests passing, ruff/mypy clean; frontend 29 → 36 tests passing,
  `tsc`/`vite build`/`oxlint` clean. **Live-verified against production**: real document
  uploaded, a real generation answer's citation resolved correctly through the new endpoint,
  unknown-chunk and cross-owner requests both 404, and deleting the source document correctly
  404s its chunk endpoint too (the panel's "no longer available" trigger). Deployed via `git
  pull` + `systemctl restart` (no migration) and `vercel --prod --scope bankar-ai` (the
  `--scope` flag was newly required this session — a bare `vercel --prod` returned "Not
  authorized" despite `vercel whoami` succeeding). Merged to `develop` via PR #49. Deferred
  follow-ups (not blockers): citation markers inside bold text aren't clickable, no responsive
  layout for narrow viewports, no keyboard/focus/aria affordances on the panel, and citations
  don't survive a conversation-history reload (pre-existing gap, made more costly by this
  feature).
- **ERP-044 (Document-Scoped Retrieval) is Done, deployed, and live-verified (2026-09-18)**:
  a caller can now scope a retrieval/generation query to a chosen subset of their own documents
  via a new `document_ids` parameter, filtered before RRF fusion on both retrieval legs — true
  search-time filtering on FAISS via `IDSelectorBatch`/`SearchParameters(sel=...)` (verified
  experimentally against real `faiss` before writing production code, not a weaker post-hoc
  filter), and a SQL `IN` clause on BM25. `document_ids=None` (omitted) is unchanged
  "search everything owned" behavior; `document_ids=[]` (explicit) short-circuits to no results.
  Frontend: the Chat sidebar's document list gained a checkbox per document (checked by default,
  opt-out model), wired into every query. Backend 485 → 502 tests passing, ruff/mypy clean;
  frontend `tsc`/`vite build`/`oxlint` clean, 29 vitest tests passing (no new tests for
  `ChatPage.tsx`, which had no prior coverage — verified live instead). **Live-verified against
  the production deployment**: two documents with mutually-exclusive content uploaded; scoped
  retrieval to one, the other, both (unscoped), and explicitly-empty all matched the design
  exactly; a full streamed generation query scoped to one document produced a correct, grounded,
  cited answer. Test user and its data deleted afterward via the admin API. Deployed via `git
  pull` + `systemctl restart` on the VM (no migration) and `vercel --prod`. Merged to `develop`
  via PR #48. **ERP-050 (visual/UX redesign) remains open**, deliberately deferred as its own,
  separately-scoped design effort — see `.ai/tickets/ERP-050.md`.
- **ERP-066 (2026-09-18)**: `DELETE /admin/users/{id}` 500'd for any user with a rated message
  -- a real regression from ERP-045 (below), caught during this session's own post-deploy
  cleanup, not a user report. `delete_user_and_owned_data` didn't know about the new
  `message_feedback` table; fixed, regression-tested, and live-verified (reproduced the exact
  `500`, deployed the fix, confirmed the same call now returns `204`).
- **A five-ticket batch (ERP-045, ERP-062, ERP-063, ERP-064, ERP-065) was completed, deployed,
  and live-verified 2026-09-18**
  from a fresh round of live UI feedback, deliberately scoped to same-session-sized work —
  **ERP-044 (document-scoped retrieval) and ERP-050 (visual redesign) were explicitly left out**
  as bigger, separately-scoped efforts. Summary:
  - **ERP-065** [Bug] — citation parsing silently dropped everything when the model wrote
    `[1, 2, 5]` instead of `[1][2][5]`; `_CITATION_MARKER_RE` now handles both forms.
  - **ERP-062** [Lapse] — renaming a conversation to a name another of the caller's own
    conversations already has (case-insensitive) is now rejected with `409`; renaming to a
    conversation's own current title is still allowed.
  - **ERP-063** [Bug] — chat rendered literal `**asterisks**` instead of markdown; new
    `frontend/src/lib/markdownLite.tsx` (bold + bullet/numbered lists, no new dependency) now
    renders it properly. `SYSTEM_PROMPT` also gained explicit structured-formatting and
    anti-hallucination guidance.
  - **ERP-064** [Lapse] — the chat pane never auto-scrolled; now scrolls to the latest message
    as it streams in.
  - **ERP-045** [Improvement] — per-message thumbs up/down feedback, finally scoped and built.
    Required exposing message IDs to the frontend for the first time (`GenerationResponse`
    gained `assistant_message_id`, the streaming `done` event gained the same, and
    `Message`/`ConversationHistoryResponse` gained `id`/`feedback`) — a real prerequisite gap,
    not just the feedback table itself. New `message_feedback` table (migration
    `2da7a6112cf0`), `PUT`/`DELETE /conversations/messages/{message_id}/feedback`.
  - Two migrations this batch: `ea444b637948` (conversations.title, from the prior session) was
    already applied; `2da7a6112cf0` (message_feedback) is new.
  - Verified: backend 461 → 485 tests passing, ruff/mypy clean; frontend 24 → 29 tests passing,
    `tsc`/`oxlint` clean. Live-verified against the real local stack (real Postgres/Redis/Ollama):
    multi-citation parsing, feedback set/switch/clear round-tripping through history reload,
    and duplicate-name 409 rejection all confirmed with real API calls before deploying.
- **The full ERP-051-059 batch was deployed and live-verified 2026-09-18** (see the entry
  below for what it contained). Root cause of a post-deploy hiccup: the backend deployed
  cleanly via `git pull` + `systemctl restart` on the VM, but **the frontend was not
  auto-deploying from GitHub at all** — Vercel had no Git integration connected, so it was
  still serving a build from 2026-09-17, predating this repo's last two sessions entirely.
  Fixed by running `vercel --prod` directly from `frontend/` to ship the current build; `vercel
  git connect` failed (needs interactive OAuth in the Vercel dashboard) so **auto-deploy on push
  is still not connected** — flagged for the user to do once via Vercel's Settings → Git. Also
  live-verified end-to-end post-deploy: `GET /documents`/`GET /conversations` both live, and a
  real generation query returned exactly one citation with `score`/`reranked` populated. Cleaned
  up 8 throwaway test accounts (7 from this session's live testing, 1 older leftover) via the
  live admin API.
- **Three more tickets (ERP-046, ERP-060, ERP-061) closed the same day**, from a live bug
  report against the freshly-redeployed frontend:
  - **ERP-046** [Lapse] — the long-open "no relevance guardrail" gap. Two-layer fix: a
    deterministic greeting-phrase fast-path (`app/generation/service.py`'s `_GREETING_RE`) skips
    retrieval/LLM entirely for "hi"/"hello there"/etc.; a distance-based relevance gate on the
    FAISS leg (`RetrievalSettings.max_relevant_distance`, new) filters out-of-scope queries
    before RRF fusion. Threshold (0.95) live-calibrated against real `nomic-embed-text`
    embeddings (measured on-topic distances 0.74-0.85 vs. off-topic 1.02-1.15), then re-verified
    against the ERP-029 evaluation harness with zero regression (exact match to the existing
    baseline).
  - **ERP-060** [Lapse] — refreshing the browser always started a blank "New chat" even though
    the sidebar's conversation list loaded fine; confirmed cause was `ChatPage.tsx` never
    persisting which conversation was active. Fixed via a per-user `localStorage` key, restored
    and its history reloaded automatically on mount.
  - **ERP-061** [Improvement] — conversations can now be renamed (new `title` column via
    migration, `PATCH /conversations/{id}`, a small inline rename affordance in the sidebar);
    an unrenamed conversation's auto-derived preview title is unchanged.
  - Verified: backend 459 → 461 tests passing, ruff/mypy clean; frontend `tsc`/`oxlint` clean,
    24 vitest tests passing. Live-verified all three against the real local stack (greeting,
    off-topic, on-topic, rename) before being committed — not yet deployed to the live VM/Vercel
    as of this writing (see session log for the pending deploy step).
- **A nine-ticket batch (ERP-051 through ERP-059) covering infra promises, upload UX, citation
  quality, and prompt-injection guardrails was completed 2026-09-18**, all code-level work
  committed to `develop` — see `.ai/sessions/2026-09-18-erp051-059-batch.md` for full detail.
  **Nothing in this batch is deployed to the live VM yet** (confirmed: the live VM is still at
  the ERP-047 merge commit) — deploying it, plus fixing ERP-057's CORS entry, both need the
  user's own action (SSH is blocked from mutating the live VM by the environment's auto-mode
  classifier, same as `gh pr merge` throughout this project's history). Summary:
  - **ERP-058** [Lapse, security] — explicit prompt-injection guardrails. Retrieved content is
    now wrapped in `<untrusted_context>` tags with an explicit system-prompt defense.
    **Live-verified against real Ollama with a real before/after test**: the old prompt was
    genuinely exploitable (a malicious chunk made `gemma3:4b` reply "PWNED."); the new one
    correctly resisted the identical attack. Framed as a mitigation, not a guarantee.
  - **ERP-055** [Bug] — citations now only list chunks the answer actually references via a
    `[n]` marker, not every chunk stuffed into the LLM's context window (the concrete cause of
    the reported "more citations than necessary" feel). Changed the streaming SSE event order
    (`citations` now fires after tokens, not before) since which chunks were cited can only be
    known once the answer exists.
  - **ERP-056** [Improvement] — `Citation` gained `score`/`reranked` fields (previously
    computed internally, never surfaced); frontend shows them in an expandable per-citation
    detail row.
  - **ERP-053** [Lapse] — a manual `POST /ingestion/jobs/{job_id}/retry` endpoint re-runs a
    failed job against its original uploaded bytes (also fixed a real pre-existing leak: temp
    upload files were never cleaned up on success or failure).
  - **ERP-054** [Improvement] — Documents page gained drag-and-drop, multi-file upload, a
    visible size-limit label, and a real byte-level upload progress bar (`XMLHttpRequest`, not
    `fetch`, which has no reliable progress event).
  - **ERP-051** [Improvement, verification] — upload ceiling finalized at 20MB (was an arbitrary
    50MB). **Live-verified**: a realistic ~15MB image-heavy PDF processes in ~75-90s; a
    deliberately pathological 18MB/1305-page dense-text PDF took 30+ minutes (VM stayed healthy
    throughout, never crashed, but this isn't an acceptable UX) — byte size alone doesn't bound
    worst-case processing time for unusually page-dense content.
  - **ERP-059** [Improvement, verification] — OCR/scanned-PDF path **live-verified end-to-end**
    for the first time (ERP-047's own verification used a table PDF, not a scanned one): a real
    image-based PDF was correctly routed to Cloud Run's `docling` OCR fallback
    (`parser_used: "quality"`), extracted text was byte-for-byte correct, VM memory stayed
    completely flat throughout (~388-397MB, confirming zero VM impact as designed).
  - **ERP-052** [Improvement, verification] — concurrency **live-verified**: 5 concurrent
    uploads from 5 distinct users completed in ~20s with zero failures, even while the ERP-051
    pathological job was also running in the background. Higher concurrency not yet tested
    (deliberately, to avoid compounding risk on a previously-crashed single-instance VM); **5 is
    the current tested, promised number**.
  - **ERP-057** — Vercel project renamed (free) to
    `https://bankar-ai-self-hosted-rag-platform.vercel.app`. Root cause of the resulting
    "Failed to fetch" login error confirmed via read-only SSH: the live VM's
    `CORS_ALLOWED_ORIGINS` still only lists the old URL. The fix (`sed` + service restart) was
    blocked by the auto-mode classifier as a live-production-mutating SSH command — status
    `Blocked (on user action)`, exact command recorded in `.ai/tickets/ERP-057.md`.
  - Verified (backend, this batch): ruff/mypy clean, full suite 424 -> 440 tests passing,
    ~97% coverage. Verified (frontend): `tsc --noEmit`/`oxlint` clean, 24 vitest tests passing.
  - Three throwaway test users/documents were created on the **live** deployment during this
    session's live-testing (OCR test, size-ceiling tests, concurrency test x5 users) and were
    **not cleaned up** (no admin account or `/documents`/`/admin` endpoints available on the
    currently-deployed old code to do so) — flagged for cleanup once the new code is deployed
    and an admin session is available, same pattern as ERP-038/039's leftover users (closed by
    ERP-040).
- **ERP-048 and ERP-049 are Done (2026-09-18)**, closing out the two functional gaps found
  during ERP-043's final live UI walkthrough (the walkthrough itself is now also Done — see
  below). **ERP-048** [Lapse]: conversation history didn't survive a fresh login — full history
  was always persisted server-side (ERP-018/019), but there was no `GET /conversations`
  list-mine endpoint, and separately, clicking a sidebar conversation never actually fetched its
  history at all. Both fixed: new `GET /conversations` (`app/generation/repository.py`'s
  `list_conversations_for_owner`/`get_first_user_messages`, `service.list_conversations`,
  `router.py`'s endpoint), and the frontend's `ChatPage.tsx` now hydrates the sidebar from the
  server (not `localStorage`, which was deleted along with its now-unused `conversationsStore.ts`
  module) and loads a conversation's full history via the existing `GET /conversations/{id}`
  when clicked. **ERP-049** [Improvement]: no way to delete a document, successfully ingested or
  failed. New `GET /documents`/`DELETE /documents/{id}` (`app/ingestion/repository.py`'s
  `list_documents_for_owner`/`delete_document`, new `app/embedding/service.py`'s
  `delete_document_and_vectors` orchestrating a Postgres-then-FAISS delete,
  `app/embedding/index.py` gaining `FaissIndex.remove`/`OwnerFaissIndexStore.remove` over FAISS's
  previously-unused `remove_ids`); the Documents page now shows a real "Delete" button for
  ingested documents (calling the new endpoint) and a "Dismiss" button for failed in-flight
  uploads (local-only, since a failed job never gets a Postgres row to delete). Both
  live-verified end-to-end against the real local stack (Postgres/Redis/Ollama): a real PDF
  ingested, retrieved, deleted, and a repeat retrieval confirmed both the Postgres rows *and*
  the FAISS vectors were actually gone (not just orphaned) — reproducing and then fixing the
  exact "hi conversation with an empty chat pane" scenario from the reported screenshot along
  the way. Verified: backend ruff/mypy clean, 424 tests passing (was 383), 97% coverage;
  frontend `tsc`/`oxlint` clean, 24 vitest tests passing (was 22). See `.ai/tickets/ERP-048.md`
  and `ERP-049.md` for full detail. **ERP-043 itself is now Done** (the walkthrough this ticket
  had been waiting on is complete) — see its Resolution note. **ERP-050** (visual/UX redesign)
  is opened as `Backlog`, explicitly deferred until after these two, with NotebookLM-style
  design research already attached to its notes for whenever it's picked up.
- **ERP-047 is Done (2026-09-17)** [Bug] — the live VM outage from a PDF upload is fully fixed.
  `docling`'s fallback parser now runs on a separate Cloud Run service
  (`deploy/cloud_run_docling/`), called over HTTP with GCP IAM auth (no stored secret);
  `docling`/`torch`/`transformers` (72 packages, including unneeded CUDA/nvidia wheels) are
  entirely removed from the root project and the live VM's own `.venv`. Three real bugs found
  and fixed during implementation/deployment (not just planned): a Windows temp-file-reopen
  issue caught by a local smoke test before deploying; the container downloading `docling`'s
  models from HuggingFace at *request* time and hitting HF's rate limit (fixed by baking models
  into the Docker image at build time, `docling-tools models download`, mirroring
  `deploy/modal_ollama.py`'s existing pattern, plus pointing `DocumentConverter` at that local
  path explicitly); and a missing system library (`libxcb.so.1`) for `opencv-python` on
  `python:3.12-slim`, a known "opencv in a slim Docker image" issue. Live-verified end-to-end:
  `POST /parse` returned `200 OK` with zero HuggingFace network calls, and the VM's memory held
  at 266-321Mi available throughout — matching the pre-incident baseline exactly, no spike.
  Backend: 394 tests passed (was 387), 96.62% coverage. Merged via PR #40 and a follow-up fix
  PR #41. See `.ai/tickets/ERP-047.md`'s Resolution for full detail.
- **Modal workspace-disabled issue (2026-09-17), found during ERP-047's live verification and
  resolved same-day**: the live deployment's Modal workspace (hosting Ollama for
  embedding/generation) started returning `"modal-http: workspace ... is disabled"`, blocking
  the embedding/generation stage of the pipeline (unrelated to ERP-047's own fix, which worked
  correctly throughout). Root cause confirmed via the Modal dashboard: usage had hit the $1
  usable-without-a-payment-method threshold on the Starter plan's $30/mo free credit, which
  disables the workspace until a card is added. User added a payment method and set a **$0
  spend limit** (Modal's hard-stop-on-any-real-charge setting — all workloads stop the instant
  estimated charges would exceed the $30 free credit, so this stays genuinely free forever, by
  design, rather than risking a surprise charge). Live-verified after the fix: a real
  `POST /generation/query` against the live deployment returned `200` with a correct, grounded,
  cited answer. `D:\github-projects\gcp-deployment-tracker.md`'s Modal row updated to record
  the spend-limit setting for future reference.

**Still-open tickets from ERP-043's live UI review (2026-09-17)** — categorized per the new
`Category` field convention (`.ai/tickets/README.md`), kept together here as the one place to
check what's still open from that session:

- **ERP-044** [Improvement] — Document-scoped retrieval: no way to limit a chat query to
  specific documents; `search()` always searches everything the caller owns. Needs a backend
  design pass (filter parameter threaded through retrieval/generation), not just a frontend
  tweak.
- **ERP-045** [Improvement] — Answer feedback (thumbs up/down or similar): not implemented at
  all yet, needs its own design (what's captured, where it's stored, whether it feeds ERP-030's
  evaluation harness).
- **ERP-046** [Lapse] — Retrieval has no relevance guardrail: a non-question ("hi") still
  retrieves top-k chunks and gets a fully-cited answer about an unrelated document. Works exactly
  as designed; the design never considered this case.

All three are `Status: Backlog`, un-started. ERP-043 itself (Web UI) is deployed and live at
`https://bankar-ai-self-hosted-rag-platform.vercel.app` (renamed 2026-09-18 from the
auto-generated `frontend-sigma-one-54.vercel.app`, free — a Vercel project rename), iterated
through two live-review bugfix rounds
(PRs #38, #39: SPA-routing 404 on refresh, cross-user localStorage leakage, a page that could
hang forever on a slow `/auth/me` fetch — all fixed), and is now `Status: Done` (2026-09-18)
after a final walkthrough surfaced two more real gaps, fixed the same day as ERP-048/ERP-049
(see the "Next Planned Work" entry above), plus ERP-050 opened as deferred visual-polish backlog.

**Older deferred items:**

- Self-service identity linking for an already-logged-in local user to add an OIDC identity (ERP-032 only supports auto-link-by-verified-email during login, not an explicit "link my account" flow).
- Additional OIDC providers beyond Google (Microsoft Entra ID, Okta, self-hosted Keycloak/Authentik) are supported by ERP-032's provider-agnostic design but not concretely verified end-to-end yet.
- Admin cross-user data visibility — the `admin` role is currently a distinction only (checked, but no elevated privilege); every ownership check is a bare `owner_id` equality with no admin bypass. Deferred rather than added untested at the tail of ERP-026 (surfaced by the final whole-branch review).
- Self-service admin account creation — deliberately not exposed via `POST /auth/register`; still a manual/repository-level step (see ERP-040's resolution note for how the live deployment's first admin, `ops-admin@self-hosted-rag-platform.internal`, was created this way), deferred for future follow-up.
- Operators with pre-ERP-031 ingested data should run `uv run python -m app.embedding.migrate_to_per_owner` before deploying ERP-031, then manually remove the old shared `data/faiss_index.bin` once the new per-owner indexes are confirmed correct.
