"""Orchestrates one production-sampling run (ERP-097).

Scores real, already-persisted `conversation_messages` assistant turns against the same
`GenerationJudge` interface the golden-dataset harness uses (`app.evaluation.judges`), so
`app.evaluation.generation_runner`'s scoring plumbing is reused, not duplicated. Deliberately
out-of-band: this never runs on the live chat request path (`app.generation.service`), only via
a separate manual invocation (`app.evaluation.production_sample_run`), so it can never add
latency to a real user's request.

Every unsampled assistant message is scored (not a probabilistic sample) -- given this
deployment's current low/sporadic traffic, that's what "every evaluation metric calculated
against it" (the actual goal) means in practice. `limit` bounds how many messages one
invocation processes, so a single run's Ollama/judge-LLM load stays predictable; repeated
invocations catch up over time. If traffic grows enough that scoring every message becomes
too expensive, this is the seam to add a sampling rate at, not a redesign.
"""

from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_session_factory
from app.evaluation.judges import GenerationJudge, RagasJudge
from app.evaluation.metrics import mean_excluding_none
from app.evaluation.repository import (
    get_preceding_user_message,
    get_unsampled_assistant_messages,
    save_production_sample_score,
)
from app.evaluation.schemas import ProductionSampleResult, ProductionSamplingSummary
from app.ingestion.repository import get_chunks_by_ids


def run_production_sampling(
    judge: GenerationJudge | None = None,
    limit: int = 50,
    session_factory: sessionmaker[Session] | None = None,
) -> ProductionSamplingSummary:
    """Score up to `limit` not-yet-sampled real assistant messages and persist each result.

    A message is skipped (not scored) rather than crashing the run when: it has no preceding
    user message (shouldn't happen via the real app), it has no citations at all (e.g. a
    greeting or "not enough information" short-circuit), or every cited chunk's document has
    since been deleted. A skipped message still gets a `ProductionSampleScoreRecord` (all score
    fields `None`), so it's never retried on a later run.
    """
    judge = judge or RagasJudge()
    session_factory = session_factory or get_session_factory()
    judge_name = type(judge).__name__

    results: list[ProductionSampleResult] = []
    with session_factory() as session:
        candidates = get_unsampled_assistant_messages(session, limit)
        for message in candidates:
            preceding = get_preceding_user_message(session, message)
            if preceding is None:
                result = ProductionSampleResult(
                    message_id=message.id,
                    conversation_id=message.conversation_id,
                    query="",
                    judge=judge_name,
                    skipped=True,
                    skip_reason="no preceding user message found",
                )
                results.append(result)
                save_production_sample_score(session, result)
                continue

            citation_chunk_ids = [
                citation["chunk_id"] for citation in (message.citations or []) if "chunk_id" in citation
            ]
            chunks_by_id = get_chunks_by_ids(session, citation_chunk_ids)
            contexts = [
                chunks_by_id[chunk_id].text for chunk_id in citation_chunk_ids if chunk_id in chunks_by_id
            ]

            if not contexts:
                result = ProductionSampleResult(
                    message_id=message.id,
                    conversation_id=message.conversation_id,
                    query=preceding.content,
                    judge=judge_name,
                    skipped=True,
                    skip_reason="no resolvable retrieved context (no citations, or all cited chunks deleted)",
                )
                results.append(result)
                save_production_sample_score(session, result)
                continue

            scores = judge.score(preceding.content, message.content, contexts)
            result = ProductionSampleResult(
                message_id=message.id,
                conversation_id=message.conversation_id,
                query=preceding.content,
                judge=judge_name,
                faithfulness=scores.faithfulness,
                answer_relevancy=scores.answer_relevancy,
                context_precision=scores.context_precision,
            )
            results.append(result)
            save_production_sample_score(session, result)

        session.commit()

    scored = [r for r in results if not r.skipped]
    faithfulness_mean, _ = mean_excluding_none(r.faithfulness for r in scored)
    relevancy_mean, _ = mean_excluding_none(r.answer_relevancy for r in scored)
    precision_mean, _ = mean_excluding_none(r.context_precision for r in scored)

    return ProductionSamplingSummary(
        judge=judge_name,
        num_candidates=len(results),
        num_scored=len(scored),
        num_skipped=len(results) - len(scored),
        mean_faithfulness=faithfulness_mean,
        mean_answer_relevancy=relevancy_mean,
        mean_context_precision=precision_mean,
    )
