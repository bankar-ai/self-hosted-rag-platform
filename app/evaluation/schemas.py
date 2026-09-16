"""Pydantic schemas for one evaluation run's per-query results and aggregate summary."""

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
