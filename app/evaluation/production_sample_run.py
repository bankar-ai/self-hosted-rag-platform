"""CLI entrypoint: `uv run python -m app.evaluation.production_sample_run [--judge ragas|ollama] [--limit N]`.

Scores up to `--limit` not-yet-sampled real production assistant messages and prints a report.
This module's stdout report output is a deliberate, documented exception to the no-`print()`
rule, same as `app.evaluation.run`/`app.evaluation.generation_run`.
"""

import argparse

from app.evaluation.judges import OllamaLLMClientJudge, RagasJudge
from app.evaluation.production_sampling import run_production_sampling
from app.generation.client import OllamaLLMClient
from app.generation.config import get_generation_settings


def main() -> None:
    """Parse `--judge`/`--limit`, run the production sampling harness, and print a report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["ragas", "ollama"], default="ragas")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    judge = (
        RagasJudge()
        if args.judge == "ragas"
        else OllamaLLMClientJudge(OllamaLLMClient(get_generation_settings()))
    )

    summary = run_production_sampling(judge=judge, limit=args.limit)

    print(f"Production sampling run ({summary.judge}): {summary.num_candidates} candidate message(s)")
    print(f"  Scored:  {summary.num_scored}")
    print(f"  Skipped: {summary.num_skipped} (no resolvable context)")
    if summary.num_scored > 0:
        print(f"  Mean Faithfulness:      {summary.mean_faithfulness:.3f}")
        print(f"  Mean Answer Relevancy:  {summary.mean_answer_relevancy:.3f}")
        print(f"  Mean Context Precision: {summary.mean_context_precision:.3f}")


if __name__ == "__main__":
    main()
