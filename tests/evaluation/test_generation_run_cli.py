"""Tests for app.evaluation.generation_run's threshold-gating logic (ERP-083)."""

from app.evaluation.generation_run import check_thresholds
from app.evaluation.schemas import GenerationEvaluationSummary


def _summary(**overrides) -> GenerationEvaluationSummary:
    defaults = {
        "judge": "ollama",
        "num_queries": 4,
        "mean_faithfulness": 0.9,
        "mean_answer_relevancy": 0.85,
        "mean_context_precision": 0.8,
        "per_query": [],
    }
    defaults.update(overrides)
    return GenerationEvaluationSummary(**defaults)


def test_no_thresholds_given_never_fails():
    assert check_thresholds(_summary(), None, None, None) == []


def test_passes_when_all_metrics_meet_their_thresholds():
    summary = _summary(mean_faithfulness=0.9, mean_answer_relevancy=0.85, mean_context_precision=0.8)
    assert check_thresholds(summary, 0.7, 0.7, 0.7) == []


def test_fails_when_faithfulness_drops_below_threshold():
    summary = _summary(mean_faithfulness=0.2)
    failures = check_thresholds(summary, 0.7, None, None)
    assert len(failures) == 1
    assert "Faithfulness" in failures[0]


def test_fails_independently_for_each_metric_below_threshold():
    summary = _summary(mean_faithfulness=0.1, mean_answer_relevancy=0.1, mean_context_precision=0.1)
    failures = check_thresholds(summary, 0.7, 0.7, 0.7)
    assert len(failures) == 3
