"""Generation-quality judges: score an (input, response, contexts) triple.

`RagasJudge` (default) uses Ragas's Faithfulness/Answer-Relevancy/Context-Precision metrics
against a local Ollama judge model. `OllamaLLMClientJudge` is a selectable fallback using
hand-written prompts against the existing `OllamaLLMClient`, for when Ragas's documented
local-Ollama reliability issues (timeouts) make the default judge unusable -- see
`docs/superpowers/specs/2026-09-06-generation-evaluation-design.md`.
"""

import asyncio
import logging
import re
from typing import Protocol

from app.embedding.config import get_embedding_settings
from app.evaluation.schemas import GenerationScores
from app.generation.client import LLMClient
from app.generation.config import get_generation_settings

logger = logging.getLogger(__name__)

_SCORE_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)")


class GenerationJudge(Protocol):
    """Anything that can score one generated answer's quality against its context."""

    def score(self, user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores:
        """Score `response` (answering `user_input`) against `retrieved_contexts`."""
        ...


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_score(raw_response: str, metric_name: str) -> float | None:
    match = _SCORE_PATTERN.search(raw_response)
    if match is None:
        logger.warning(
            "Could not parse a numeric score for %s from judge response: %r", metric_name, raw_response
        )
        return None
    return _clamp(float(match.group(1)))


def _score_with_retry(
    llm_client: LLMClient, system_prompt: str, user_prompt: str, metric_name: str
) -> float | None:
    """Call `llm_client`, parse a score, and retry once on a parse failure before giving up.

    Returns `None` (not `0.0`) if both attempts fail to parse -- a parse failure is not the same
    as a judge-assigned low score, and must stay distinguishable from one downstream.
    """
    score = _parse_score(llm_client.generate(system_prompt, user_prompt), metric_name)
    if score is not None:
        return score

    score = _parse_score(llm_client.generate(system_prompt, user_prompt), metric_name)
    if score is None:
        logger.warning(
            "Judge response for %s was still unparseable after one retry; recording as unavailable, not 0.0",
            metric_name,
        )
    return score


_FAITHFULNESS_PROMPT = (
    "You are grading whether an answer's claims are all supported by the given context.\n"
    "Context:\n{context}\n\nAnswer:\n{response}\n\n"
    "Respond with 'Score: X' where X is a number from 0.0 (no claims supported) to 1.0 "
    "(every claim supported), followed by a one-sentence justification."
)
_ANSWER_RELEVANCY_PROMPT = (
    "You are grading whether an answer actually addresses the question asked.\n"
    "Question:\n{user_input}\n\nAnswer:\n{response}\n\n"
    "Respond with 'Score: X' where X is a number from 0.0 (does not address the question) to "
    "1.0 (directly and fully addresses it), followed by a one-sentence justification."
)
_CONTEXT_PRECISION_PROMPT = (
    "You are grading what fraction of the retrieved context was actually relevant to answering "
    "the question.\nQuestion:\n{user_input}\n\nContext:\n{context}\n\n"
    "Respond with 'Score: X' where X is a number from 0.0 (none relevant) to 1.0 (all relevant), "
    "followed by a one-sentence justification."
)
_JUDGE_SYSTEM_PROMPT = "You are a strict, precise evaluator of RAG system outputs."


class OllamaLLMClientJudge:
    """`GenerationJudge` using hand-written prompts against an existing `LLMClient`."""

    def __init__(self, llm_client: LLMClient) -> None:
        """Build a judge that scores using `llm_client` (e.g. `OllamaLLMClient`)."""
        self._llm_client = llm_client

    def score(self, user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores:
        """Score `response` via three separate judge-LLM calls, one per metric.

        Each metric is retried once on an unparseable response (see `_score_with_retry`); a
        metric that still can't be parsed after the retry is `None`, not `0.0`.
        """
        context = "\n---\n".join(retrieved_contexts)

        return GenerationScores(
            faithfulness=_score_with_retry(
                self._llm_client,
                _JUDGE_SYSTEM_PROMPT,
                _FAITHFULNESS_PROMPT.format(context=context, response=response),
                "faithfulness",
            ),
            answer_relevancy=_score_with_retry(
                self._llm_client,
                _JUDGE_SYSTEM_PROMPT,
                _ANSWER_RELEVANCY_PROMPT.format(user_input=user_input, response=response),
                "answer_relevancy",
            ),
            context_precision=_score_with_retry(
                self._llm_client,
                _JUDGE_SYSTEM_PROMPT,
                _CONTEXT_PRECISION_PROMPT.format(user_input=user_input, context=context),
                "context_precision",
            ),
        )


class RagasJudge:
    """`GenerationJudge` using Ragas's Faithfulness/ResponseRelevancy/LLMContextPrecisionWithoutReference."""

    def __init__(self) -> None:
        """Build Ragas metric scorers backed by local Ollama models (judge LLM + embeddings)."""
        from langchain_ollama import ChatOllama, OllamaEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

        generation_settings = get_generation_settings()
        embedding_settings = get_embedding_settings()
        judge_llm = LangchainLLMWrapper(
            ChatOllama(model=generation_settings.model, base_url=generation_settings.ollama_host)
        )
        judge_embeddings = LangchainEmbeddingsWrapper(
            OllamaEmbeddings(model=embedding_settings.model, base_url=embedding_settings.ollama_host)
        )
        self._faithfulness = Faithfulness(llm=judge_llm)
        self._answer_relevancy = ResponseRelevancy(llm=judge_llm, embeddings=judge_embeddings)
        self._context_precision = LLMContextPrecisionWithoutReference(llm=judge_llm)

    def score(self, user_input: str, response: str, retrieved_contexts: list[str]) -> GenerationScores:
        """Score `response` via three Ragas metrics, each an async call run to completion here."""
        from ragas import SingleTurnSample

        sample = SingleTurnSample(
            user_input=user_input, response=response, retrieved_contexts=retrieved_contexts
        )
        return GenerationScores(
            faithfulness=asyncio.run(self._faithfulness.single_turn_ascore(sample)),
            answer_relevancy=asyncio.run(self._answer_relevancy.single_turn_ascore(sample)),
            context_precision=asyncio.run(self._context_precision.single_turn_ascore(sample)),
        )
