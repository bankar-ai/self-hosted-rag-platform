from unittest.mock import AsyncMock, patch

from app.evaluation.judges import OllamaLLMClientJudge


class _StubLLMClient:
    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def generate(self, system_prompt, user_prompt):
        return next(self._responses)

    def generate_stream(self, system_prompt, user_prompt):
        raise NotImplementedError


def test_ollama_judge_parses_well_formed_scores():
    client = _StubLLMClient(
        [
            "Score: 0.9 -- every claim is grounded in the context.",
            "Score: 0.7 -- mostly answers the question.",
            "Score: 0.5 -- half the retrieved context is relevant.",
        ]
    )
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness == 0.9
    assert scores.answer_relevancy == 0.7
    assert scores.context_precision == 0.5


def test_ollama_judge_clamps_out_of_range_scores():
    client = _StubLLMClient(["Score: 1.5", "Score: -0.2", "Score: 0.5"])
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness == 1.0
    assert scores.answer_relevancy == 0.0
    assert scores.context_precision == 0.5


def test_ollama_judge_retries_once_and_uses_the_retry_score_on_first_parse_failure():
    client = _StubLLMClient(
        [
            "I cannot determine a score.",  # faithfulness: first attempt unparseable
            "Score: 0.6",  # faithfulness: retry succeeds
            "Score: 0.5",
            "Score: 0.5",
        ]
    )
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness == 0.6


def test_ollama_judge_records_none_not_zero_when_still_unparseable_after_retry():
    client = _StubLLMClient(
        [
            "I cannot determine a score.",  # faithfulness: first attempt unparseable
            "Still no score.",  # faithfulness: retry also unparseable
            "Score: 0.5",
            "Score: 0.5",
        ]
    )
    judge = OllamaLLMClientJudge(client)

    scores = judge.score("q", "a", ["context"])

    assert scores.faithfulness is None
    assert scores.answer_relevancy == 0.5
    assert scores.context_precision == 0.5


def test_ragas_judge_wires_scores_from_each_metric():
    with (
        patch("langchain_ollama.ChatOllama"),
        patch("langchain_ollama.OllamaEmbeddings"),
        patch("ragas.llms.LangchainLLMWrapper"),
        patch("ragas.embeddings.LangchainEmbeddingsWrapper"),
        patch("ragas.metrics.Faithfulness") as mock_faithfulness_cls,
        patch("ragas.metrics.ResponseRelevancy") as mock_relevancy_cls,
        patch("ragas.metrics.LLMContextPrecisionWithoutReference") as mock_precision_cls,
    ):
        mock_faithfulness_cls.return_value.single_turn_ascore = AsyncMock(return_value=0.9)
        mock_relevancy_cls.return_value.single_turn_ascore = AsyncMock(return_value=0.8)
        mock_precision_cls.return_value.single_turn_ascore = AsyncMock(return_value=0.7)

        from app.evaluation.judges import RagasJudge

        judge = RagasJudge()
        scores = judge.score("question", "answer", ["context one"])

        assert scores.faithfulness == 0.9
        assert scores.answer_relevancy == 0.8
        assert scores.context_precision == 0.7
