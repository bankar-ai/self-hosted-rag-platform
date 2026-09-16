"""CLI entrypoint: `uv run python -m app.evaluation.generation_run [--judge ragas|ollama]`.

Runs the generation-quality evaluation harness and prints a report. This module's stdout
report output is a deliberate, documented exception to the no-`print()` rule, same as
`app.evaluation.run`.
"""

import argparse

from app.evaluation.generation_runner import run_generation_evaluation
from app.evaluation.judges import OllamaLLMClientJudge, RagasJudge
from app.generation.client import OllamaLLMClient
from app.generation.config import get_generation_settings


def main() -> None:
    """Parse `--judge`, run the evaluation harness, and print a summary report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["ragas", "ollama"], default="ragas")
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


if __name__ == "__main__":
    main()
