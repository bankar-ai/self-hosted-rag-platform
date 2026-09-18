"""Hybrid search over ingested chunks.

Fuses FAISS vector search and Postgres BM25 full-text search via Reciprocal Rank Fusion (RRF),
then hydrates the fused results from Postgres. See `.ai/adr/ADR-005.md` for why RRF was chosen
over score-normalization-based fusion.
"""

import hashlib
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.core.telemetry import get_tracer
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import OwnerFaissIndexStore
from app.ingestion.repository import (
    get_chunks_by_vector_ids,
    get_sibling_chunks,
    get_vector_ids_for_documents,
    search_chunks_by_text,
)
from app.retrieval.cache import RetrievalCache, get_default_retrieval_cache
from app.retrieval.config import RetrievalSettings, get_reranker_settings, get_retrieval_settings
from app.retrieval.reranker import FlashRankReranker, Reranker
from app.retrieval.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

# The constant from the original RRF paper (Cormack et al.), and the value most hybrid-search
# implementations default to. Dampens the influence of low ranks without per-corpus tuning.
RRF_K = 60

# Each retriever is asked for RRF_OVERSAMPLE_MULTIPLIER * top_k candidates before fusion, so a
# chunk that ranks just outside top_k on one retriever but strongly on the other still has a
# chance to fuse into the final top_k. Without oversampling, fusion only ever sees the union of
# two already-truncated top_k lists, defeating the point of combining two signals.
RRF_OVERSAMPLE_MULTIPLIER = 4


def _reciprocal_rank_fusion(*ranked_id_lists: list[int], k: int = RRF_K) -> list[tuple[int, float]]:
    """Fuse multiple rank-ordered ID lists into one, scored by normalized reciprocal rank.

    Each `ranked_id_lists` entry is a best-first list of IDs from one retriever. An ID's raw
    contribution from a given list is `1 / (k + rank)` (1-indexed); IDs absent from a list
    contribute nothing for that list. Raw sums are then divided by the maximum score achievable
    (an ID ranked first in every non-empty list), so the fused score is bounded to `(0, 1]` and
    comparable across queries -- `1.0` means "best possible rank in every retriever that found
    it". This is a fusion-confidence score, not a similarity/distance metric. Returns `(id,
    normalized_score)` pairs sorted by score descending. `[]` if every input list is empty.
    """
    scores: dict[int, float] = {}
    for ranked_ids in ranked_id_lists:
        for rank, item_id in enumerate(ranked_ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    if not scores:
        return []
    max_possible_score = sum(1.0 / (k + 1) for ranked_ids in ranked_id_lists if ranked_ids)
    normalized = [(item_id, score / max_possible_score) for item_id, score in scores.items()]
    return sorted(normalized, key=lambda pair: pair[1], reverse=True)


def _cache_key(
    query: str,
    top_k: int,
    rerank: bool,
    expand_sections: bool,
    owner_id: uuid.UUID,
    document_ids: list[str] | None,
) -> str:
    """Hash the parameters that determine `search()`'s output, for cache lookups.

    `owner_id` is part of the key -- without it, one user's cached results could leak to
    another user issuing the same query text. `document_ids` (ERP-044) is sorted before
    hashing so the same set in a different order still hits the same cache entry; `None`
    (unrestricted) and `[]` (explicitly empty) hash differently from each other and from any
    real set, since they're semantically distinct.
    """
    query_bytes = query.encode()
    owner_bytes = str(owner_id).encode()
    document_ids_bytes = (
        "\x00".join(sorted(document_ids)).encode() if document_ids is not None else b"\x01"
    )
    payload = (
        len(query_bytes).to_bytes(4, "big")
        + query_bytes
        + top_k.to_bytes(4, "big")
        + bytes([rerank, expand_sections])
        + owner_bytes
        + document_ids_bytes
    )
    return hashlib.sha256(payload).hexdigest()


def _expand_sections(session: Session, results: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Insert each result's section-siblings immediately after it, deduped, best-first.

    Every not-yet-seen chunk in `results` is followed by any other chunk in its document
    sharing its exact `section_path` (excluding chunks already seen), inheriting that anchor's
    `score`. Preserves `results`' relative order; may return more items than went in. See
    `.ai/adr/ADR-007.md` for why this section-sibling approach was chosen.
    """
    expanded: list[RetrievedChunk] = []
    seen_chunk_ids: set[str] = set()
    for anchor in results:
        if anchor.chunk_id in seen_chunk_ids:
            continue
        expanded.append(anchor)
        seen_chunk_ids.add(anchor.chunk_id)

        siblings = get_sibling_chunks(session, anchor.document_id, anchor.section_path, seen_chunk_ids)
        for sibling in siblings:
            expanded.append(
                RetrievedChunk(
                    chunk_id=sibling.chunk_id,
                    document_id=sibling.document_id,
                    text=sibling.text,
                    section_path=sibling.section_path,
                    page_start=sibling.page_start,
                    page_end=sibling.page_end,
                    source_filename=sibling.source_filename,
                    score=anchor.score,
                )
            )
            seen_chunk_ids.add(sibling.chunk_id)
    return expanded


def search(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index_store: OwnerFaissIndexStore | None = None,
    rerank: bool = False,
    reranker: Reranker | None = None,
    expand_sections: bool = False,
    cache: RetrievalCache | None = None,
    retrieval_settings: RetrievalSettings | None = None,
    document_ids: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Run hybrid (vector + BM25) search restricted to `owner_id`'s documents.

    Returns up to `top_k` chunks, fused-score order. `embedding_client`/`faiss_index_store`/
    `settings` are injectable for testing; default to Ollama/local-disk implementations
    built from `settings` (or the process-wide cached `EmbeddingSettings` if `settings` is
    not given).

    If `rerank` is true, the fused+hydrated results are re-scored and reordered by
    `reranker` (a `FlashRankReranker` built from the process-wide `RerankerSettings` if none
    is injected) before being returned. If `rerank` is false (the default), `reranker` is
    never constructed or invoked, so opting out costs nothing.

    If `expand_sections` is true, the (possibly reranked) results are expanded with each
    result's section-siblings (see `_expand_sections`) -- the returned list may then be
    longer than `top_k`; this is intended, not a bug.

    `cache` is an injectable `RetrievalCache` (defaulting to `RedisRetrievalCache`); the
    full result of this function, keyed by (`query`, `top_k`, `rerank`, `expand_sections`,
    `owner_id`), is cache-aside -- a hit returns immediately without running any of the
    pipeline below.

    Isolation is now structural (ERP-031): `faiss_index_store` is an `OwnerFaissIndexStore`,
    which searches only `owner_id`'s own physically-separate index file -- there is no shared
    index to filter after the fact, so another owner's vectors are never candidates in the
    first place. BM25 is independently scoped by the same `owner_id` via a SQL join
    (`search_chunks_by_text`). Oversampling candidates (`RRF_OVERSAMPLE_MULTIPLIER`) is kept
    purely for RRF fusion quality now, not for isolation.

    `retrieval_settings.max_relevant_distance` (ERP-046), if set, drops vector-leg candidates
    whose raw FAISS L2 distance exceeds it *before* RRF fusion -- an off-topic query (e.g. a
    plain greeting) that would otherwise still surface `top_k` results regardless of how
    genuinely relevant they are. BM25 is unaffected (its own `ts_rank` is a real relevance
    signal already). If this drops every vector candidate and BM25 also finds nothing,
    `search()` returns `[]` exactly as it already does for an empty index.

    `document_ids` (ERP-044), if given, restricts both legs to only chunks from those
    documents -- `None` (the default) searches everything `owner_id` owns, unchanged from
    before this parameter existed; `[]` explicitly searches nothing and returns `[]`
    immediately, distinct from `None`. The vector leg is restricted via FAISS's own
    `IDSelectorBatch` at search time (not a post-hoc filter over an unrestricted top-`k`,
    which could miss a real match entirely if the target document wasn't already in the top
    `candidate_k` overall); the BM25 leg is restricted via a SQL `IN` clause.
    """
    if document_ids == []:
        return []

    cache = cache or get_default_retrieval_cache()
    cache_key = _cache_key(query, top_k, rerank, expand_sections, owner_id, document_ids)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index_store = faiss_index_store or OwnerFaissIndexStore(
        settings.faiss_index_dir, settings.dimension
    )

    candidate_k = top_k * RRF_OVERSAMPLE_MULTIPLIER

    vectors = embedding_client.embed([query])
    if not vectors:
        raise ValueError("embedding client returned no vectors for the query")

    session_factory = get_session_factory()
    with session_factory() as session:
        allowed_vector_ids = (
            get_vector_ids_for_documents(session, owner_id, document_ids)
            if document_ids is not None
            else None
        )
        vector_hits = faiss_index_store.search(
            owner_id, vectors[0], candidate_k, allowed_vector_ids=allowed_vector_ids
        )
        retrieval_settings = retrieval_settings or get_retrieval_settings()
        if retrieval_settings.max_relevant_distance is not None:
            vector_hits = [
                (vector_id, distance)
                for vector_id, distance in vector_hits
                if distance <= retrieval_settings.max_relevant_distance
            ]
        vector_ranked_ids = [vector_id for vector_id, _ in vector_hits]

        bm25_hits = search_chunks_by_text(
            session, query, candidate_k, owner_id, document_ids=document_ids
        )
        bm25_ranked_ids = [vector_id for vector_id, _ in bm25_hits]

        with get_tracer().start_as_current_span("retrieval.fuse") as span:
            fused = _reciprocal_rank_fusion(vector_ranked_ids, bm25_ranked_ids)[:top_k]
            span.set_attribute("retrieval.fused_count", len(fused))
        if not fused:
            cache.set(cache_key, [])
            return []

        chunks_by_vector_id = get_chunks_by_vector_ids(
            session, [vector_id for vector_id, _ in fused], owner_id
        )
        results = []
        for vector_id, score in fused:
            chunk = chunks_by_vector_id.get(vector_id)
            if chunk is None:
                logger.warning("Dropping fused hit with no matching chunk row: vector_id=%s", vector_id)
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_filename=chunk.source_filename,
                    score=score,
                )
            )

        if rerank:
            with get_tracer().start_as_current_span("retrieval.rerank") as span:
                reranker = reranker or FlashRankReranker(get_reranker_settings())
                results = reranker.rerank(query, results)
                span.set_attribute("retrieval.reranked_count", len(results))

        if expand_sections:
            with get_tracer().start_as_current_span("retrieval.expand_sections") as span:
                results = _expand_sections(session, results)
                span.set_attribute("retrieval.expanded_count", len(results))

        cache.set(cache_key, results)
        return results
