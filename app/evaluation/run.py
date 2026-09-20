"""CLI entrypoint: `uv run python -m app.evaluation.run [--fail-under-precision P] [...]`.

Runs the retrieval-quality evaluation harness and prints a report. This module's stdout
report output is a deliberate, documented exception to the no-`print()` rule (a CLI tool's
whole purpose is stdout output) -- not a violation to fix.

The `--fail-under-*` flags (ERP-083) are what let this double as a CI gate: omitted (the
default, and every existing manual invocation), this behaves exactly as before -- print a
report, exit 0. Given one or more, this exits 1 if the corresponding metric falls below it,
mirroring this repo's existing `--cov-fail-under` convention for the pytest coverage gate.
"""

import argparse
import sys

from app.evaluation.runner import run_evaluation
from app.evaluation.schemas import EvaluationSummary


def check_thresholds(
    summary: EvaluationSummary,
    fail_under_precision: float | None,
    fail_under_recall: float | None,
    fail_under_mrr: float | None,
) -> list[str]:
    """Return a description of each threshold `summary` falls below. `[]` means it passes."""
    failures = []
    if fail_under_precision is not None and summary.mean_precision < fail_under_precision:
        failures.append(f"Precision@{summary.top_k} {summary.mean_precision:.3f} < {fail_under_precision}")
    if fail_under_recall is not None and summary.mean_recall < fail_under_recall:
        failures.append(f"Recall@{summary.top_k} {summary.mean_recall:.3f} < {fail_under_recall}")
    if fail_under_mrr is not None and summary.mrr < fail_under_mrr:
        failures.append(f"MRR {summary.mrr:.3f} < {fail_under_mrr}")
    return failures


def main() -> None:
    """Parse `--fail-under-*`, run the evaluation harness, print a report, and gate on it."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-under-precision", type=float, default=None)
    parser.add_argument("--fail-under-recall", type=float, default=None)
    parser.add_argument("--fail-under-mrr", type=float, default=None)
    args = parser.parse_args()

    summary = run_evaluation()

    print(f"Evaluation run: {summary.num_queries} queries, top_k={summary.top_k}")
    print(f"  Mean Precision@{summary.top_k}: {summary.mean_precision:.3f}")
    print(f"  Mean Recall@{summary.top_k}:    {summary.mean_recall:.3f}")
    print(f"  MRR:                    {summary.mrr:.3f}")
    print()
    for result in summary.per_query:
        print(f"  [{result.reciprocal_rank:.2f} RR] {result.query!r}")
        print(f"      retrieved: {result.retrieved_chunk_ids}")
        print(f"      relevant:  {result.relevant_chunk_ids}")

    failures = check_thresholds(
        summary, args.fail_under_precision, args.fail_under_recall, args.fail_under_mrr
    )
    if failures:
        print()
        print("FAILED threshold checks:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
