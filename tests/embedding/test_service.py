import uuid

from app.core.db import get_session_factory
from app.embedding.config import EmbeddingSettings
from app.embedding.index import OwnerFaissIndexStore
from app.embedding.service import delete_document_and_vectors, embed_and_persist
from app.ingestion.models import ChunkRecord, DocumentRecord
from app.ingestion.schemas import Chunk

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


class _FakeEmbeddingClient:
    def __init__(self, dimension):
        self._dimension = dimension
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[0.1] * self._dimension for _ in texts]


def _chunk(document_id: str, index: int) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=f"chunk text {index}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        char_count=13,
        parser_used="fast",
        source_filename="doc.pdf",
    )


def test_embed_and_persist_writes_to_postgres_and_faiss(tmp_path):
    document_id = "doc-service-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    fake_client = _FakeEmbeddingClient(dimension=4)
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    settings = EmbeddingSettings(dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    embed_and_persist(
        document_id=document_id,
        source_filename="doc.pdf",
        chunks=chunks,
        owner_id=_TEST_OWNER_ID,
        settings=settings,
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert fake_client.calls == [["chunk text 0", "chunk text 1"]]
    assert len(faiss_index_store.search(_TEST_OWNER_ID, [0.1] * 4, k=10)) == 2

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = (
            session.query(ChunkRecord)
            .filter(ChunkRecord.document_id == document_id)
            .order_by(ChunkRecord.chunk_index)
            .all()
        )
        assert [row.chunk_id for row in rows] == ["doc-service-test-0", "doc-service-test-1"]


def test_embed_and_persist_noop_for_empty_chunks(tmp_path):
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    fake_client = _FakeEmbeddingClient(dimension=4)

    embed_and_persist(
        document_id="doc-empty",
        source_filename="doc.pdf",
        chunks=[],
        owner_id=_TEST_OWNER_ID,
        settings=EmbeddingSettings(dimension=4),
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    assert fake_client.calls == []
    assert faiss_index_store.search(_TEST_OWNER_ID, [0.1] * 4, k=10) == []


def test_delete_document_and_vectors_removes_postgres_rows_and_faiss_vectors(tmp_path):
    document_id = "doc-delete-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]
    fake_client = _FakeEmbeddingClient(dimension=4)
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    settings = EmbeddingSettings(dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    embed_and_persist(
        document_id=document_id,
        source_filename="doc.pdf",
        chunks=chunks,
        owner_id=_TEST_OWNER_ID,
        settings=settings,
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )
    assert len(faiss_index_store.search(_TEST_OWNER_ID, [0.1] * 4, k=10)) == 2

    deleted = delete_document_and_vectors(
        document_id, _TEST_OWNER_ID, settings=settings, faiss_index_store=faiss_index_store
    )

    assert deleted is True
    assert faiss_index_store.search(_TEST_OWNER_ID, [0.1] * 4, k=10) == []
    with session_factory() as session:
        assert session.get(DocumentRecord, document_id) is None
        assert (
            session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).count() == 0
        )


def test_delete_document_and_vectors_returns_false_for_unknown_document(tmp_path):
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    settings = EmbeddingSettings(dimension=4)

    deleted = delete_document_and_vectors(
        "does-not-exist", _TEST_OWNER_ID, settings=settings, faiss_index_store=faiss_index_store
    )

    assert deleted is False


def test_delete_document_and_vectors_returns_false_for_wrong_owner(tmp_path):
    document_id = "doc-delete-wrong-owner-test"
    other_owner_id = uuid.uuid4()
    fake_client = _FakeEmbeddingClient(dimension=4)
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    settings = EmbeddingSettings(dimension=4)

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        from app.auth.models import UserRecord

        session.add(UserRecord(id=other_owner_id, email=f"{other_owner_id}@test", hashed_password="x"))
        session.commit()

    embed_and_persist(
        document_id=document_id,
        source_filename="doc.pdf",
        chunks=[_chunk(document_id, 0)],
        owner_id=_TEST_OWNER_ID,
        settings=settings,
        embedding_client=fake_client,
        faiss_index_store=faiss_index_store,
    )

    deleted = delete_document_and_vectors(
        document_id, other_owner_id, settings=settings, faiss_index_store=faiss_index_store
    )

    assert deleted is False
    with session_factory() as session:
        assert session.get(DocumentRecord, document_id) is not None
