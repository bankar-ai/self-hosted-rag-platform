import uuid

import pytest

from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import OwnerFaissIndexStore
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk
from app.retrieval import service as service_module
from app.retrieval.config import RetrievalSettings
from app.retrieval.schemas import RetrievedChunk
from app.retrieval.service import (
    RRF_OVERSAMPLE_MULTIPLIER,
    _expand_sections,
    _reciprocal_rank_fusion,
    search,
)

_TEST_OWNER_ID = uuid.uuid4()

# These fusion-mechanics tests use synthetic orthogonal unit vectors as stand-ins for "distinct
# content," not real embedding geometry -- their L2 distance (sqrt(2) =~ 1.41) would otherwise
# trip ERP-046's live-calibrated relevance gate, which is irrelevant to what these tests check.
_NO_RELEVANCE_GATE = RetrievalSettings(max_relevant_distance=None)


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


class _FakeEmbeddingClient:
    def __init__(self, vector):
        self._vector = vector
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [self._vector for _ in texts]


class _FakeReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, candidates):
        self.calls.append((query, candidates))
        return list(reversed(candidates))


def _chunk(
    document_id: str, index: int, text: str | None = None, section_path: list[str] | None = None
) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=text if text is not None else f"chunk text {index}",
        section_path=section_path if section_path is not None else ["Intro"],
        page_start=1,
        page_end=1,
        char_count=13,
        parser_used="fast",
        source_filename="doc.pdf",
    )


def _persist_and_index(document_id, chunks, vectors, faiss_index_store, owner_id):
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, owner_id)
        session.commit()
        vector_ids = [record.vector_id for record in records]
    faiss_index_store.add(owner_id, vector_ids, vectors)
    return vector_ids


def test_search_does_not_invoke_reranker_when_rerank_is_false(tmp_path):
    document_id = "doc-norerank-test"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_reranker = _FakeReranker()
    search(
        query="find it",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        rerank=False,
        reranker=fake_reranker,
    )

    assert fake_reranker.calls == []


def test_search_invokes_injected_reranker_and_uses_its_order_when_rerank_is_true(tmp_path):
    document_id = "doc-rerank-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_reranker = _FakeReranker()
    results = search(
        query="find chunk 0",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        rerank=True,
        reranker=fake_reranker,
    )

    assert len(fake_reranker.calls) == 1
    called_query, called_candidates = fake_reranker.calls[0]
    assert called_query == "find chunk 0"
    # _FakeReranker reverses order; service.search's own fused order had chunk 0 first
    # (asserted by the equivalent non-reranked test), so a reversed result confirms the
    # reranker's output -- not the fused order -- is what's returned.
    assert [chunk.chunk_id for chunk in called_candidates] == list(reversed([c.chunk_id for c in results]))


def test_search_returns_ranked_chunks(tmp_path):
    document_id = "doc-search-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find chunk 0",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        retrieval_settings=_NO_RELEVANCE_GATE,
    )

    assert fake_client.calls == [["find chunk 0"]]
    assert len(results) == 2
    assert results[0].chunk_id == "doc-search-test-0"
    assert results[0].score > results[1].score


def test_search_on_empty_index_returns_empty_list(tmp_path):
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])

    results = search(
        query="anything",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert results == []


def test_search_top_k_larger_than_available_returns_all(tmp_path):
    document_id = "doc-search-test-2"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find it",
        top_k=10,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert len(results) == 1


def test_search_drops_orphaned_faiss_hit_with_no_matching_chunk_row(tmp_path, caplog, monkeypatch):
    document_id = "doc-search-test-3"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    vector_ids = _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    # Index a vector_id that was never persisted to Postgres, simulating the two stores
    # having diverged (see ERP-011's "Known Limitations": writes are not atomic). Since
    # ERP-031, the owner's index is otherwise exclusively theirs, so this orphan reaches
    # hydration directly -- no owner-filter step sits in between anymore.
    orphan_vector_id = 999_999_999
    faiss_index_store.add(_TEST_OWNER_ID, [orphan_vector_id], [[1.0, 0.0, 0.0, 0.0]])

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    with caplog.at_level("WARNING"):
        results = search(
            query="find it",
            top_k=10,
            owner_id=_TEST_OWNER_ID,
            settings=EmbeddingSettings(dimension=4),
            embedding_client=fake_client,
            faiss_index_store=faiss_index_store,
        )

    assert len(results) == 1
    assert results[0].chunk_id == f"{document_id}-0"
    assert vector_ids[0] != orphan_vector_id
    assert any(str(orphan_vector_id) in record.message for record in caplog.records)


def test_search_fuses_bm25_hits_when_vector_search_finds_nothing(tmp_path):
    document_id = "doc-bm25-test"
    # Distinctive terms (not reused elsewhere in the suite) so full-text matches from other
    # tests' persisted chunks in the shared test database can't bleed into this assertion.
    chunks = [_chunk(document_id, 0, text="quokkas snorkel past zorbonium reefs at duskfall")]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    # No vectors added to FAISS at all -> vector retriever returns nothing.

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()
        vector_ids = [record.vector_id for record in records]

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="quokkas zorbonium",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert len(results) == 1
    assert results[0].chunk_id == f"{document_id}-0"
    assert vector_ids


def test_search_requests_oversampled_candidates_from_each_retriever_before_fusion(
    tmp_path, monkeypatch
):
    document_id = "doc-oversample-test"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    recorded_faiss_k = []
    original_faiss_search = faiss_index_store.search

    def _spy_faiss_search(owner_id, vector, k):
        recorded_faiss_k.append(k)
        return original_faiss_search(owner_id, vector, k)

    monkeypatch.setattr(faiss_index_store, "search", _spy_faiss_search)

    recorded_bm25_k = []
    original_bm25_search = service_module.search_chunks_by_text

    def _spy_bm25_search(session, query, k, owner_id):
        recorded_bm25_k.append(k)
        return original_bm25_search(session, query, k, owner_id)

    monkeypatch.setattr(service_module, "search_chunks_by_text", _spy_bm25_search)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    search(
        query="chunk text 0",
        top_k=3,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    # Each retriever must be asked for more than `top_k` candidates -- fusing two already
    # top_k-truncated lists would only ever see their union (at most 2 * top_k items),
    # defeating the point of combining two ranking signals. See RRF_OVERSAMPLE_MULTIPLIER.
    assert recorded_faiss_k == [3 * RRF_OVERSAMPLE_MULTIPLIER]
    assert recorded_bm25_k == [3 * RRF_OVERSAMPLE_MULTIPLIER]


def test_search_fuses_overlapping_vector_and_bm25_hits(tmp_path):
    document_id = "doc-hybrid-test"
    chunks = [
        _chunk(document_id, 0, text="wombats burrow beneath flarnwood thickets"),
        _chunk(document_id, 1, text="the stock market closed lower on Tuesday"),
    ]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="wombats flarnwood",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        retrieval_settings=_NO_RELEVANCE_GATE,
    )

    # Note: BM25 searches the whole `chunks` table (no per-document filtering, per ERP-012/014
    # scope), so other tests' persisted chunks may also lexically match and appear in results;
    # this test only asserts on this document's own two chunks and their relative order.
    assert results[0].chunk_id == f"{document_id}-0"
    result_chunk_ids = [result.chunk_id for result in results]
    assert f"{document_id}-0" in result_chunk_ids
    assert f"{document_id}-1" in result_chunk_ids
    assert result_chunk_ids.index(f"{document_id}-0") < result_chunk_ids.index(f"{document_id}-1")


def test_expand_sections_inserts_unseen_sibling_after_anchor_with_anchors_score():
    document_id = "doc-expand-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 1, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 2, section_path=["Chapter 1", "Methods"]),
    ]
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    anchor = RetrievedChunk(
        chunk_id=f"{document_id}-0",
        document_id=document_id,
        text="chunk text 0",
        section_path=["Chapter 1", "Background"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.42,
    )

    with session_factory() as session:
        expanded = _expand_sections(session, [anchor])

    assert [chunk.chunk_id for chunk in expanded] == [f"{document_id}-0", f"{document_id}-1"]
    assert expanded[1].score == 0.42


def test_expand_sections_does_not_duplicate_an_already_present_chunk():
    document_id = "doc-expand-dedup-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Intro"]),
        _chunk(document_id, 1, section_path=["Intro"]),
    ]
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    anchors = [
        RetrievedChunk(
            chunk_id=f"{document_id}-{index}",
            document_id=document_id,
            text=f"chunk text {index}",
            section_path=["Intro"],
            page_start=1,
            page_end=1,
            source_filename="doc.pdf",
            score=1.0 - index * 0.1,
        )
        for index in (0, 1)
    ]

    with session_factory() as session:
        expanded = _expand_sections(session, anchors)

    assert [chunk.chunk_id for chunk in expanded] == [f"{document_id}-0", f"{document_id}-1"]


def test_search_with_expand_sections_true_appends_section_siblings(tmp_path):
    document_id = "doc-search-expand-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 1, section_path=["Chapter 1", "Background"]),
    ]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find chunk 0",
        top_k=1,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        expand_sections=True,
    )

    # top_k=1 means only chunk 0 is a direct hit, but expand_sections pulls in its sibling.
    assert [chunk.chunk_id for chunk in results] == [f"{document_id}-0", f"{document_id}-1"]


def test_search_with_expand_sections_false_matches_baseline(tmp_path):
    document_id = "doc-search-noexpand-test"
    chunks = [_chunk(document_id, 0, section_path=["Chapter 1", "Background"])]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="find chunk 0",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert [chunk.chunk_id for chunk in results] == [f"{document_id}-0"]


def test_search_composes_rerank_and_expand_sections(tmp_path):
    document_id = "doc-search-rerank-expand-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 1, section_path=["Chapter 1", "Background"]),
    ]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_reranker = _FakeReranker()
    results = search(
        query="find chunk 0",
        top_k=1,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        rerank=True,
        reranker=fake_reranker,
        expand_sections=True,
    )

    assert len(fake_reranker.calls) == 1
    assert [chunk.chunk_id for chunk in results] == [f"{document_id}-0", f"{document_id}-1"]


def test_reciprocal_rank_fusion_sums_reciprocal_ranks_across_lists():
    fused = dict(_reciprocal_rank_fusion([1, 2, 3], [2, 1], k=60))
    max_possible = 2 * (1 / 61)  # two non-empty lists -> best case is rank 1 in both
    assert fused[1] == pytest.approx((1 / 61 + 1 / 62) / max_possible)
    assert fused[2] == pytest.approx((1 / 62 + 1 / 61) / max_possible)
    assert fused[3] == pytest.approx((1 / 63) / max_possible)
    assert all(0 < score <= 1.0 for score in fused.values())


def test_reciprocal_rank_fusion_handles_one_empty_list():
    fused = dict(_reciprocal_rank_fusion([5, 6], [], k=60))
    max_possible = 1 / 61  # only one non-empty list -> best case is rank 1 in that list
    assert [vector_id for vector_id in fused] == [5, 6]
    assert fused[5] == pytest.approx((1 / 61) / max_possible)
    assert fused[5] == 1.0
    assert fused[6] == pytest.approx((1 / 62) / max_possible)


def test_reciprocal_rank_fusion_both_empty_returns_empty_list():
    assert _reciprocal_rank_fusion([], [], k=60) == []


class _FakeRetrievalCache:
    def __init__(self, preset: dict[str, list] | None = None):
        self.store = dict(preset or {})
        self.get_calls = []
        self.set_calls = []

    def get(self, cache_key):
        self.get_calls.append(cache_key)
        return self.store.get(cache_key)

    def set(self, cache_key, results):
        self.set_calls.append((cache_key, results))
        self.store[cache_key] = results


def test_search_returns_cached_result_without_touching_pipeline(tmp_path):
    cached_results = [
        RetrievedChunk(
            chunk_id="cached-0",
            document_id="doc-cached",
            text="cached text",
            section_path=["Intro"],
            page_start=1,
            page_end=1,
            source_filename="doc.pdf",
            score=1.0,
        )
    ]
    fake_cache = _FakeRetrievalCache()
    cache_key = service_module._cache_key("cached query", 5, False, False, _TEST_OWNER_ID)
    fake_cache.store[cache_key] = cached_results

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)

    results = search(
        query="cached query",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        cache=fake_cache,
    )

    assert results == cached_results
    assert fake_client.calls == []  # embedding client never called on a cache hit


def test_search_populates_cache_on_miss(tmp_path):
    document_id = "doc-cache-miss-test"
    chunks = [_chunk(document_id, 0)]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_cache = _FakeRetrievalCache()

    results = search(
        query="find it",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        cache=fake_cache,
    )

    cache_key = service_module._cache_key("find it", 5, False, False, _TEST_OWNER_ID)
    assert fake_cache.get_calls == [cache_key]
    assert fake_cache.set_calls == [(cache_key, results)]


def test_search_caches_empty_result_on_miss(tmp_path):
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_cache = _FakeRetrievalCache()

    results = search(
        query="anything",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        cache=fake_cache,
    )

    cache_key = service_module._cache_key("anything", 5, False, False, _TEST_OWNER_ID)
    assert results == []
    assert fake_cache.set_calls == [(cache_key, [])]


def test_cache_key_differs_by_rerank_and_expand_sections_flags():
    base = service_module._cache_key("q", 5, False, False, _TEST_OWNER_ID)
    assert service_module._cache_key("q", 5, True, False, _TEST_OWNER_ID) != base
    assert service_module._cache_key("q", 5, False, True, _TEST_OWNER_ID) != base
    assert service_module._cache_key("q", 10, False, False, _TEST_OWNER_ID) != base


def test_cache_key_differs_by_owner_id():
    base = service_module._cache_key("q", 5, False, False, _TEST_OWNER_ID)
    assert service_module._cache_key("q", 5, False, False, uuid.uuid4()) != base


def test_search_never_returns_another_owners_vector_hit_regardless_of_raw_similarity(tmp_path):
    """ERP-031: isolation is now structural, not a post-fusion filter.

    Three owners each ingest a document, sharing one `OwnerFaissIndexStore` instance (one
    directory, but a physically separate index *file* per owner underneath it). Every
    document's embedding is a real (if imperfect) semantic match for the query, but the
    calling owner's own vector is deliberately the *worst* match of the three by raw vector
    similarity -- the other two owners' vectors are closer to the query. The query text is
    disjoint from every chunk's lexical content, so BM25 finds nothing for anyone and this
    isolates the vector-only path.

    Before ERP-031, this scenario was exactly the failure mode of the shared-index design
    (see the ERP-026 whole-branch-review finding this test used to guard against): with
    `top_k=1`, truncation-before-owner-filtering could hand the caller's only result slot to
    a *better-raw-similarity* foreign vector, leaving the caller with 0 results. After
    ERP-031, no such failure mode can exist at all: the other owners' vectors are never even
    candidates in `faiss_index_store.search(target_owner, ...)`, because they were never
    added to `target_owner`'s index file -- there is no filter step to have a bug in.
    """
    from app.auth.models import UserRecord

    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_ids = [uuid.uuid4() for _ in range(3)]
    # Best-to-worst raw vector similarity to the query vector [1, 0, 0, 0]; the *last* owner
    # (worst match) is the one whose isolation we're testing.
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.99, 0.01, 0.0, 0.0], [0.9, 0.1, 0.0, 0.0]]

    session_factory = get_session_factory()
    with session_factory() as session:
        for owner_id in owner_ids:
            session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.commit()

    for owner_id, vector in zip(owner_ids, vectors, strict=True):
        document_id = f"doc-isolation-{owner_id}"
        # Distinctive nonsense words, absent from the query below, so BM25 (full-text) finds
        # no match for anyone -- this test is isolating the vector-only path.
        chunks = [_chunk(document_id, 0, text="zqlorp fribbentast wobsedge quennifer")]
        _persist_and_index(document_id, chunks, [vector], faiss_index_store, owner_id)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    target_owner = owner_ids[-1]  # the worst-ranked-by-raw-vector-similarity owner
    results = search(
        query="completely unrelated query text",
        top_k=1,
        owner_id=target_owner,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert len(results) == 1
    assert results[0].chunk_id == f"doc-isolation-{target_owner}-0"


def test_two_owners_faiss_data_lives_in_physically_separate_files(tmp_path):
    """Structural proof of isolation, independent of any `search()` behavior.

    Each owner's vectors are written to their own file on disk -- there is no single shared
    file that both owners' data could ever coexist in.
    """
    from app.auth.models import UserRecord

    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    document_a, document_b = f"doc-file-isolation-a-{owner_a}", f"doc-file-isolation-b-{owner_b}"

    session_factory = get_session_factory()
    with session_factory() as session:
        session.add(UserRecord(id=owner_a, email=f"{owner_a}@test", hashed_password="x"))
        session.add(UserRecord(id=owner_b, email=f"{owner_b}@test", hashed_password="x"))
        session.commit()

    _persist_and_index(
        document_a, [_chunk(document_a, 0)], [[1.0, 0.0, 0.0, 0.0]], faiss_index_store, owner_a
    )
    _persist_and_index(
        document_b, [_chunk(document_b, 0)], [[0.0, 1.0, 0.0, 0.0]], faiss_index_store, owner_b
    )

    assert (tmp_path / f"{owner_a}.bin").exists()
    assert (tmp_path / f"{owner_b}.bin").exists()
    assert faiss_index_store.search(owner_a, [1.0, 0.0, 0.0, 0.0], k=10) != []
    assert faiss_index_store.search(owner_b, [1.0, 0.0, 0.0, 0.0], k=10) != []
    # Cross-check: owner A's file alone, loaded fresh, contains only owner A's vector.
    fresh_store_a = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    hits = fresh_store_a.search(owner_a, [0.0, 1.0, 0.0, 0.0], k=10)
    assert len(hits) == 1  # owner B's much-closer-matching vector is not in owner A's file


def test_search_raises_when_embedding_client_returns_no_vectors(tmp_path):
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    fake_client.embed = lambda texts: []  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="no vectors"):
        search(
            query="anything",
            top_k=5,
            owner_id=_TEST_OWNER_ID,
            settings=EmbeddingSettings(dimension=4),
            embedding_client=fake_client,
            faiss_index_store=faiss_index_store,
        )


def test_search_relevance_gate_drops_a_vector_hit_beyond_max_relevant_distance(tmp_path):
    document_id = "doc-relevance-gate-test"
    chunks = [_chunk(document_id, 0, text="completely unrelated filler content")]
    # The query vector is orthogonal to the chunk's vector (L2 distance sqrt(2) =~ 1.41),
    # simulating an off-topic query against real content.
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[0.0, 1.0, 0.0, 0.0])
    results = search(
        query="hi",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        retrieval_settings=RetrievalSettings(max_relevant_distance=1.0),
    )

    assert results == []


def test_search_relevance_gate_keeps_a_vector_hit_within_max_relevant_distance(tmp_path):
    document_id = "doc-relevance-gate-keep-test"
    chunks = [_chunk(document_id, 0, text="closely matching content")]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[1.0, 0.0, 0.0, 0.0])
    results = search(
        query="closely matching",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        retrieval_settings=RetrievalSettings(max_relevant_distance=1.0),
    )

    assert len(results) == 1
    assert results[0].chunk_id == f"{document_id}-0"


def test_search_relevance_gate_disabled_when_max_relevant_distance_is_none(tmp_path):
    document_id = "doc-relevance-gate-disabled-test"
    chunks = [_chunk(document_id, 0, text="completely unrelated filler content")]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    _persist_and_index(document_id, chunks, vectors, faiss_index_store, _TEST_OWNER_ID)

    fake_client = _FakeEmbeddingClient(vector=[0.0, 1.0, 0.0, 0.0])
    results = search(
        query="hi",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
        retrieval_settings=RetrievalSettings(max_relevant_distance=None),
    )

    assert len(results) == 1
