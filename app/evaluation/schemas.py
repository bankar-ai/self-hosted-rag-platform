"""Pydantic schemas for one evaluation run's per-query results and aggregate summary."""

import uuid

from pydantic import BaseModel


class QueryResult(BaseModel):
    """One golden query's scored result."""

    query: str
    precision: float
    recall: float
    reciprocal_rank: float
    retrieved_chunk_ids: list[str]
    relevant_chunk_ids: list[str]


class EvaluationSummary(BaseModel):
    """Aggregate result of one full evaluation run."""

    top_k: int
    num_queries: int
    mean_precision: float
    mean_recall: float
    mrr: float
    per_query: list[QueryResult]


class GenerationScores(BaseModel):
    """One query's generation-quality scores, each in [0.0, 1.0].

    A field is `None` if the judge's response for that metric was unparseable even after a
    retry (see `app.evaluation.judges`) -- a `None` must never be treated as a score of `0.0`.
    """

    faithfulness: float | None
    answer_relevancy: float | None
    context_precision: float | None


class GenerationQueryResult(BaseModel):
    """One golden query's generated answer and its scored quality."""

    query: str
    answer: str
    faithfulness: float | None
    answer_relevancy: float | None
    context_precision: float | None


class GenerationEvaluationSummary(BaseModel):
    """Aggregate result of one full generation-quality evaluation run.

    Each `mean_*` is averaged only over the queries where that metric was parseable; the
    corresponding `*_parse_failures` count is how many queries were excluded, so a run with
    failures is visibly different from one without, rather than silently averaging in 0.0s.
    """

    judge: str
    num_queries: int
    mean_faithfulness: float
    mean_answer_relevancy: float
    mean_context_precision: float
    faithfulness_parse_failures: int = 0
    answer_relevancy_parse_failures: int = 0
    context_precision_parse_failures: int = 0
    per_query: list[GenerationQueryResult]


class ProductionSampleResult(BaseModel):
    """One live production assistant message's scored (or skipped) sampling result (ERP-097).

    `skipped=True` means no retrieved context could be resolved for this message (every cited
    chunk/document has since been deleted, or it had no citations at all) -- all three score
    fields are `None` in that case, and `skip_reason` explains why.
    """

    message_id: uuid.UUID
    conversation_id: uuid.UUID
    query: str
    judge: str
    skipped: bool = False
    skip_reason: str | None = None
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None


class ProductionSamplingSummary(BaseModel):
    """Aggregate result of one production-sampling run (ERP-097)."""

    judge: str
    num_candidates: int
    num_scored: int
    num_skipped: int
    mean_faithfulness: float
    mean_answer_relevancy: float
    mean_context_precision: float
