"""Orchestrates one full generation-quality evaluation run.

Ingests the golden dataset (same as `app.evaluation.runner`'s retrieval-quality harness),
generates a real answer for each golden query via the real pipeline building blocks, scores
each with the selected judge, persists a summary, and cleans up.
"""

import os
import shutil
import tempfile
import uuid
from collections.abc import Iterable

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.embedding.client import EmbeddingClient
from app.embedding.config import get_embedding_settings
from app.embedding.index import OwnerFaissIndexStore
from app.embedding.service import embed_and_persist
from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES
from app.evaluation.judges import GenerationJudge, RagasJudge
from app.evaluation.repository import cleanup_eval_data, save_generation_evaluation_run
from app.evaluation.schemas import GenerationEvaluationSummary, GenerationQueryResult
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.ingestion.schemas import Chunk
from app.retrieval.service import search


def _mean_and_failures(scores: Iterable[float | None]) -> tuple[float, int]:
    """Return (mean of the non-`None` scores, count of `None`s) -- `0.0`/`0` if `scores` is empty."""
    scores = list(scores)
    present = [score for score in scores if score is not None]
    failures = len(scores) - len(present)
    return (sum(present) / len(present) if present else 0.0, failures)


def run_generation_evaluation(
    judge: GenerationJudge | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    faiss_index_store: OwnerFaissIndexStore | None = None,
    top_k: int = 3,
) -> GenerationEvaluationSummary:
    """Run the golden dataset through the real generation pipeline and return a scored summary.

    `judge` defaults to `RagasJudge()`; pass `OllamaLLMClientJudge(...)` to use the fallback.
    Same isolation/cleanup lifecycle as `app.evaluation.runner.run_evaluation`: a dedicated
    temp-dir-backed `OwnerFaissIndexStore` (never the real app's persisted index), and every
    transient row this run creates (eval user, documents, chunks) is deleted before
    returning -- only the summary row persists.
    """
    judge = judge or RagasJudge()
    generation_settings = get_generation_settings()
    llm_client = llm_client or OllamaLLMClient(generation_settings)

    owned_temp_index_dir: str | None = None
    if faiss_index_store is None:
        owned_temp_index_dir = f"{tempfile.gettempdir()}/gen-eval-{uuid.uuid4().hex}"
        faiss_index_store = OwnerFaissIndexStore(owned_temp_index_dir, get_embedding_settings().dimension)

    session_factory = get_session_factory()
    with session_factory() as session:
        eval_user = create_user(session, f"gen-eval-{uuid.uuid4()}@internal", "!")
        session.commit()
        owner_id = eval_user.id

    all_document_ids: list[str] = []
    for eval_document in GOLDEN_DOCUMENTS:
        document_id = str(uuid.uuid4())
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
            embedding_client=embedding_client,
            faiss_index_store=faiss_index_store,
        )

    per_query: list[GenerationQueryResult] = []
    for eval_query in GOLDEN_QUERIES:
        chunks_found = search(
            query=eval_query.query,
            top_k=top_k,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index_store=faiss_index_store,
        )
        user_prompt, included_chunks = build_prompt(
            eval_query.query, chunks_found, generation_settings.max_context_chars
        )
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        contexts = [chunk.text for chunk in included_chunks]

        scores = judge.score(eval_query.query, answer, contexts)
        per_query.append(
            GenerationQueryResult(
                query=eval_query.query,
                answer=answer,
                faithfulness=scores.faithfulness,
                answer_relevancy=scores.answer_relevancy,
                context_precision=scores.context_precision,
            )
        )

    faithfulness_mean, faithfulness_failures = _mean_and_failures(r.faithfulness for r in per_query)
    relevancy_mean, relevancy_failures = _mean_and_failures(r.answer_relevancy for r in per_query)
    precision_mean, precision_failures = _mean_and_failures(r.context_precision for r in per_query)

    summary = GenerationEvaluationSummary(
        judge=type(judge).__name__,
        num_queries=len(per_query),
        mean_faithfulness=faithfulness_mean,
        mean_answer_relevancy=relevancy_mean,
        mean_context_precision=precision_mean,
        faithfulness_parse_failures=faithfulness_failures,
        answer_relevancy_parse_failures=relevancy_failures,
        context_precision_parse_failures=precision_failures,
        per_query=per_query,
    )

    with session_factory() as session:
        save_generation_evaluation_run(session, summary)
        cleanup_eval_data(session, all_document_ids, owner_id)
        session.commit()

    if owned_temp_index_dir is not None and os.path.exists(owned_temp_index_dir):
        shutil.rmtree(owned_temp_index_dir)

    return summary
