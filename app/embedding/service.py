"""Orchestrates embedding a document's chunks and persisting them to Postgres + FAISS."""

import uuid

from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient, OllamaEmbeddingClient
from app.embedding.config import EmbeddingSettings, get_embedding_settings
from app.embedding.index import OwnerFaissIndexStore
from app.ingestion.repository import delete_document, save_document_and_chunks
from app.ingestion.schemas import Chunk


def embed_and_persist(
    document_id: str,
    source_filename: str,
    chunks: list[Chunk],
    owner_id: uuid.UUID,
    settings: EmbeddingSettings | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index_store: OwnerFaissIndexStore | None = None,
) -> None:
    """Embed `chunks`, persist them to Postgres, and add their vectors to `owner_id`'s FAISS index.

    No-op if `chunks` is empty. `embedding_client`/`faiss_index_store` are injectable for
    testing; default to Ollama/local-disk implementations built from `settings` (or the
    process-wide cached `EmbeddingSettings` if `settings` is not given).
    """
    if not chunks:
        return

    settings = settings or get_embedding_settings()
    embedding_client = embedding_client or OllamaEmbeddingClient(settings)
    faiss_index_store = faiss_index_store or OwnerFaissIndexStore(
        settings.faiss_index_dir, settings.dimension
    )

    vectors = embedding_client.embed([chunk.text for chunk in chunks])

    session_factory = get_session_factory()
    with session_factory() as session:
        records = save_document_and_chunks(session, document_id, source_filename, chunks, owner_id)
        vector_ids = [record.vector_id for record in records]
        session.commit()

    faiss_index_store.add(owner_id, vector_ids, vectors)


def delete_document_and_vectors(
    document_id: str,
    owner_id: uuid.UUID,
    settings: EmbeddingSettings | None = None,
    faiss_index_store: OwnerFaissIndexStore | None = None,
) -> bool:
    """Delete `document_id` (and its chunks) if owned by `owner_id`, and remove its FAISS vectors.

    Returns `False` without deleting anything if the document doesn't exist or isn't owned
    by `owner_id`; `True` on success. Postgres rows are deleted and committed first -- if
    FAISS removal ever failed after that, a stale FAISS entry is harmless (already handled
    by `retrieval.service.search`'s "dropped fused hit with no matching chunk row" path),
    whereas the reverse ordering could leave an orphaned Postgres row after a failed FAISS
    write, which would not be harmless.
    """
    settings = settings or get_embedding_settings()
    faiss_index_store = faiss_index_store or OwnerFaissIndexStore(
        settings.faiss_index_dir, settings.dimension
    )

    session_factory = get_session_factory()
    with session_factory() as session:
        vector_ids = delete_document(session, document_id, owner_id)
        if vector_ids is None:
            return False
        session.commit()

    faiss_index_store.remove(owner_id, vector_ids)
    return True
