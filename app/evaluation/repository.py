"""Persistence for evaluation run summaries, and cleanup of an eval run's transient data."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserRecord
from app.evaluation.models import (
    EvaluationRunRecord,
    GenerationEvaluationRunRecord,
    ProductionSampleScoreRecord,
)
from app.evaluation.schemas import EvaluationSummary, GenerationEvaluationSummary, ProductionSampleResult
from app.generation.models import ConversationMessageRecord
from app.ingestion.models import ChunkRecord, DocumentRecord


def save_evaluation_run(session: Session, summary: EvaluationSummary) -> EvaluationRunRecord:
    """Persist `summary` as a new `EvaluationRunRecord`. Does not commit."""
    record = EvaluationRunRecord(
        top_k=summary.top_k,
        num_queries=summary.num_queries,
        mean_precision_at_k=summary.mean_precision,
        mean_recall_at_k=summary.mean_recall,
        mrr=summary.mrr,
        details=[result.model_dump() for result in summary.per_query],
    )
    session.add(record)
    session.flush()
    return record


def save_generation_evaluation_run(
    session: Session, summary: GenerationEvaluationSummary
) -> GenerationEvaluationRunRecord:
    """Persist `summary` as a new `GenerationEvaluationRunRecord`. Does not commit."""
    record = GenerationEvaluationRunRecord(
        judge=summary.judge,
        num_queries=summary.num_queries,
        mean_faithfulness=summary.mean_faithfulness,
        mean_answer_relevancy=summary.mean_answer_relevancy,
        mean_context_precision=summary.mean_context_precision,
        details=[result.model_dump() for result in summary.per_query],
    )
    session.add(record)
    session.flush()
    return record


def cleanup_eval_data(session: Session, document_ids: list[str], owner_id: uuid.UUID) -> None:
    """Delete the chunks, documents, and user created by one eval run. Does not commit.

    Order matters: `chunks.document_id` and `documents.owner_id` are both plain (non-cascading)
    foreign keys, so children must be deleted before their parents.
    """
    for document_id in document_ids:
        session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).delete()
    for document_id in document_ids:
        session.query(DocumentRecord).filter(DocumentRecord.document_id == document_id).delete()
    session.query(UserRecord).filter(UserRecord.id == owner_id).delete()


def get_unsampled_assistant_messages(session: Session, limit: int) -> list[ConversationMessageRecord]:
    """Assistant messages with no `ProductionSampleScoreRecord` yet, oldest first (ERP-097).

    A message already scored by a prior sampling run (successfully or as a skip) is excluded,
    so repeated invocations never re-score the same message.
    """
    already_sampled = select(ProductionSampleScoreRecord.message_id)
    return list(
        session.scalars(
            select(ConversationMessageRecord)
            .where(
                ConversationMessageRecord.role == "assistant",
                ConversationMessageRecord.id.not_in(already_sampled),
            )
            .order_by(ConversationMessageRecord.sequence)
            .limit(limit)
        )
    )


def get_preceding_user_message(
    session: Session, assistant_message: ConversationMessageRecord
) -> ConversationMessageRecord | None:
    """Return the `"user"` message immediately before `assistant_message` -- its question (ERP-097).

    `sequence` is a single `Identity` shared across *all* conversations, not per-conversation, so
    this filters by `conversation_id` explicitly rather than assuming `sequence - 1`. `None` if
    no such message exists (shouldn't happen via the real app, which always persists a user turn
    before its assistant reply, but this must not crash if it somehow does).
    """
    return session.scalars(
        select(ConversationMessageRecord)
        .where(
            ConversationMessageRecord.conversation_id == assistant_message.conversation_id,
            ConversationMessageRecord.sequence < assistant_message.sequence,
            ConversationMessageRecord.role == "user",
        )
        .order_by(ConversationMessageRecord.sequence.desc())
        .limit(1)
    ).first()


def save_production_sample_score(
    session: Session, result: ProductionSampleResult
) -> ProductionSampleScoreRecord:
    """Persist `result` as a new `ProductionSampleScoreRecord`. Does not commit."""
    record = ProductionSampleScoreRecord(
        message_id=result.message_id,
        conversation_id=result.conversation_id,
        judge=result.judge,
        query=result.query,
        skipped=result.skipped,
        skip_reason=result.skip_reason,
        faithfulness=result.faithfulness,
        answer_relevancy=result.answer_relevancy,
        context_precision=result.context_precision,
    )
    session.add(record)
    session.flush()
    return record
