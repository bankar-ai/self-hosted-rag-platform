"""Pure retrieval-quality metrics: Precision@k, Recall@k, and reciprocal rank."""

from collections.abc import Iterable


def mean_excluding_none(scores: Iterable[float | None]) -> tuple[float, int]:
    """Return (mean of the non-`None` scores, count of `None`s) -- `0.0`/`0` if `scores` is empty.

    Shared by both generation-quality evaluation paths (the golden-dataset harness and ERP-097's
    live-production sampler) -- a judge-unparseable score is `None`, not `0.0`, and must not drag
    the mean toward 0 or be silently averaged in.
    """
    scores = list(scores)
    present = [score for score in scores if score is not None]
    failures = len(scores) - len(present)
    return (sum(present) / len(present) if present else 0.0, failures)


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-`k` retrieved IDs that are in `relevant`.

    Divides by `k` (not `len(retrieved)`), so returning fewer than `k` results is penalized --
    matching the standard Precision@k definition.
    """
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of all `relevant` IDs found within the top-`k` retrieved IDs.

    `0.0` if `relevant` is empty (no relevant items exist to find), rather than raising.
    """
    if not relevant:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """`1 / rank` of the first relevant ID in `retrieved` (1-indexed), or `0.0` if none is found."""
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0
