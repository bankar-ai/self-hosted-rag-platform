"""Orchestrates one full retrieval-quality evaluation run.

Ingests the golden dataset, queries it through the real hybrid search pipeline, scores the
results, persists a summary, and cleans up.
"""

import os
import shutil
import tempfile
import uuid

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient
from app.embedding.config import get_embedding_settings
from app.embedding.index import OwnerFaissIndexStore
from app.embedding.service import embed_and_persist
from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES
from app.evaluation.metrics import precision_at_k, recall_at_k, reciprocal_rank
from app.evaluation.repository import cleanup_eval_data, save_evaluation_run
from app.evaluation.schemas import EvaluationSummary, QueryResult
from app.ingestion.schemas import Chunk
from app.retrieval.service import search


def run_evaluation(
    top_k: int = 3,
    embedding_client: EmbeddingClient | None = None,
    faiss_index_store: OwnerFaissIndexStore | None = None,
) -> EvaluationSummary:
    """Run the golden dataset through the real search pipeline and return a scored summary.

    Always uses a dedicated temp-dir-backed `OwnerFaissIndexStore` when `faiss_index_store`
    isn't injected, never the real app's persisted index -- running this must never pollute
    or depend on a developer's local index. Persists the resulting summary to Postgres and
    deletes every other row it created (the eval user, its documents, its chunks) before
    returning -- the persisted summary row is the only durable trace of having run this.
    """
    owned_temp_index_dir: str | None = None
    if faiss_index_store is None:
        owned_temp_index_dir = f"{tempfile.gettempdir()}/eval-{uuid.uuid4().hex}"
        faiss_index_store = OwnerFaissIndexStore(owned_temp_index_dir, get_embedding_settings().dimension)

    session_factory = get_session_factory()
    with session_factory() as session:
        eval_user = create_user(session, f"eval-{uuid.uuid4()}@internal", "!")
        session.commit()
        owner_id = eval_user.id

    document_ids_by_label: dict[str, str] = {}
    all_document_ids: list[str] = []
    for eval_document in GOLDEN_DOCUMENTS:
        document_id = str(uuid.uuid4())
        document_ids_by_label[eval_document.label] = document_id
        all_document_ids.append(document_id)
        chunks = [
            Chunk(
                chunk_id=f"{document_id}-{eval_chunk.index}",
                document_id=document_id,
                chunk_index=eval_chunk.index,
                text=eval_chunk.text,
                section_path=eval_chunk.section_path,
                page_start=eval_chunk.page,
                page_end=eval_chunk.page,
                char_count=len(eval_chunk.text),
                parser_used="fast",
                source_filename=f"{eval_document.label}.pdf",
            )
            for eval_chunk in eval_document.chunks
        ]
        embed_and_persist(
            document_id=document_id,
            source_filename=eval_document.label,
            chunks=chunks,
            owner_id=owner_id,
            parsing_confidence="high",
            embedding_client=embedding_client,
            faiss_index_store=faiss_index_store,
        )

    per_query: list[QueryResult] = []
    for eval_query in GOLDEN_QUERIES:
        document_id = document_ids_by_label[eval_query.document_label]
        relevant_ids = {f"{document_id}-{index}" for index in eval_query.expected_chunk_indices}

        results = search(
            query=eval_query.query,
            top_k=top_k,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index_store=faiss_index_store,
        )
        retrieved_ids = [chunk.chunk_id for chunk in results]

        per_query.append(
            QueryResult(
                query=eval_query.query,
                precision=precision_at_k(retrieved_ids, relevant_ids, top_k),
                recall=recall_at_k(retrieved_ids, relevant_ids, top_k),
                reciprocal_rank=reciprocal_rank(retrieved_ids, relevant_ids),
                retrieved_chunk_ids=retrieved_ids,
                relevant_chunk_ids=sorted(relevant_ids),
            )
        )

    summary = EvaluationSummary(
        top_k=top_k,
        num_queries=len(per_query),
        mean_precision=sum(r.precision for r in per_query) / len(per_query),
        mean_recall=sum(r.recall for r in per_query) / len(per_query),
        mrr=sum(r.reciprocal_rank for r in per_query) / len(per_query),
        per_query=per_query,
    )

    with session_factory() as session:
        save_evaluation_run(session, summary)
        cleanup_eval_data(session, all_document_ids, owner_id)
        session.commit()

    if owned_temp_index_dir is not None and os.path.exists(owned_temp_index_dir):
        shutil.rmtree(owned_temp_index_dir)

    return summary
