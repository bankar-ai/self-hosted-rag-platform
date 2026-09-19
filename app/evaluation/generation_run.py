"""CLI entrypoint: `uv run python -m app.evaluation.generation_run [--judge ragas|ollama] [--fail-under-* T]`.

Runs the generation-quality evaluation harness and prints a report. This module's stdout
report output is a deliberate, documented exception to the no-`print()` rule, same as
`app.evaluation.run`.

The `--fail-under-*` flags (ERP-083) are what let this double as a CI gate: omitted (the
default, and every existing manual invocation), this behaves exactly as before -- print a
report, exit 0. A `None` mean (every query's score for that metric was unparseable) never
fails its threshold -- there's nothing to compare, and that's a distinct, already-visible
failure mode (see `*_parse_failures` in the printed report), not silently treated as 0.0.
"""

import argparse
import sys

from app.evaluation.generation_runner import run_generation_evaluation
from app.evaluation.judges import OllamaLLMClientJudge, RagasJudge
from app.evaluation.schemas import GenerationEvaluationSummary
from app.generation.client import OllamaLLMClient
from app.generation.config import get_generation_settings


def check_thresholds(
    summary: GenerationEvaluationSummary,
    fail_under_faithfulness: float | None,
    fail_under_relevancy: float | None,
    fail_under_precision: float | None,
) -> list[str]:
    """Return a description of each threshold `summary` falls below. `[]` means it passes."""
    failures = []
    if fail_under_faithfulness is not None and summary.mean_faithfulness < fail_under_faithfulness:
        failures.append(f"Faithfulness {summary.mean_faithfulness:.3f} < {fail_under_faithfulness}")
    if fail_under_relevancy is not None and summary.mean_answer_relevancy < fail_under_relevancy:
        failures.append(f"Answer Relevancy {summary.mean_answer_relevancy:.3f} < {fail_under_relevancy}")
    if fail_under_precision is not None and summary.mean_context_precision < fail_under_precision:
        failures.append(f"Context Precision {summary.mean_context_precision:.3f} < {fail_under_precision}")
    return failures


def main() -> None:
    """Parse `--judge`/`--fail-under-*`, run the evaluation harness, print a report, and gate on it."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["ragas", "ollama"], default="ragas")
    parser.add_argument("--fail-under-faithfulness", type=float, default=None)
    parser.add_argument("--fail-under-relevancy", type=float, default=None)
    parser.add_argument("--fail-under-precision", type=float, default=None)
    args = parser.parse_args()

    judge = (
        RagasJudge()
        if args.judge == "ragas"
        else OllamaLLMClientJudge(OllamaLLMClient(get_generation_settings()))
    )

    summary = run_generation_evaluation(judge=judge)

    def _fmt(score: float | None) -> str:
        return f"{score:.2f}" if score is not None else "N/A"

    print(f"Generation evaluation run ({summary.judge}): {summary.num_queries} queries")
    print(f"  Mean Faithfulness:      {summary.mean_faithfulness:.3f}")
    print(f"  Mean Answer Relevancy:  {summary.mean_answer_relevancy:.3f}")
    print(f"  Mean Context Precision: {summary.mean_context_precision:.3f}")
    print(
        "  Parse failures (excluded from means above): "
        f"faithfulness={summary.faithfulness_parse_failures} "
        f"relevancy={summary.answer_relevancy_parse_failures} "
        f"precision={summary.context_precision_parse_failures}"
    )
    print()
    for result in summary.per_query:
        print(f"  {result.query!r}")
        print(f"      answer: {result.answer[:200]}")
        print(
            f"      faithfulness={_fmt(result.faithfulness)} "
            f"relevancy={_fmt(result.answer_relevancy)} "
            f"precision={_fmt(result.context_precision)}"
        )

    failures = check_thresholds(
        summary,
        args.fail_under_faithfulness,
        args.fail_under_relevancy,
        args.fail_under_precision,
    )
    if failures:
        print()
        print("FAILED threshold checks:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
