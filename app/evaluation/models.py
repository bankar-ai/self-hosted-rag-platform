"""SQLAlchemy ORM model for persisted evaluation run summaries."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

# Import order deliberate, not alphabetical: registers conversations/conversation_messages on
# Base.metadata (FK target for ProductionSampleScoreRecord below) before Base itself is used,
# regardless of whether app.generation.models has already been imported elsewhere in this process.
import app.generation.models  # noqa: F401
from app.ingestion.models import Base


class EvaluationRunRecord(Base):
    """One persisted evaluation run's aggregate metrics and per-query breakdown."""

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    top_k: Mapped[int]
    num_queries: Mapped[int]
    mean_precision_at_k: Mapped[float]
    mean_recall_at_k: Mapped[float]
    mrr: Mapped[float]
    details: Mapped[list[dict[str, object]]] = mapped_column(JSON)


class GenerationEvaluationRunRecord(Base):
    """One persisted generation-quality evaluation run's aggregate metrics and per-query breakdown."""

    __tablename__ = "generation_evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    judge: Mapped[str]
    num_queries: Mapped[int]
    mean_faithfulness: Mapped[float]
    mean_answer_relevancy: Mapped[float]
    mean_context_precision: Mapped[float]
    details: Mapped[list[dict[str, object]]] = mapped_column(JSON)


class ProductionSampleScoreRecord(Base):
    """One live production assistant message's generation-quality score (ERP-097).

    Unlike `GenerationEvaluationRunRecord` (one row per golden-dataset run, many queries), this
    is one row per individually-sampled real `conversation_messages` row -- `message_id` is
    unique so a message is never re-scored by a later sampling run. `skipped` is true when the
    message had no resolvable retrieved context (e.g. every cited chunk/document has since been
    deleted, or the message was a short-circuit answer with no citations at all) -- the row
    still exists so the message is never retried, but its score fields are all `None`.
    """

    __tablename__ = "production_sample_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation_messages.id"), unique=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"))
    scored_at: Mapped[datetime] = mapped_column(server_default=func.now())
    judge: Mapped[str]
    query: Mapped[str]
    skipped: Mapped[bool] = mapped_column(default=False)
    skip_reason: Mapped[str | None] = mapped_column(default=None)
    faithfulness: Mapped[float | None] = mapped_column(default=None)
    answer_relevancy: Mapped[float | None] = mapped_column(default=None)
    context_precision: Mapped[float | None] = mapped_column(default=None)
