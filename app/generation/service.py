"""Grounded answer generation over hybrid-retrieved chunks, with optional multi-turn memory."""

import logging
import re
import uuid
from typing import Any, Iterator

from app.core.db import get_session_factory
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.repository import (
    append_message,
    get_all_messages,
    get_conversation,
    get_conversation_owner_id,
    get_first_user_messages,
    get_or_create_conversation,
    get_recent_messages,
    list_conversations_for_owner,
)
from app.generation.repository import (
    rename_conversation as repository_rename_conversation,
)
from app.generation.rewrite import rewrite_query
from app.generation.schemas import (
    Citation,
    ConversationHistoryResponse,
    ConversationListResponse,
    ConversationSummary,
    ConversationTurn,
    GenerationResponse,
    Message,
)
from app.retrieval.schemas import RetrievedChunk
from app.retrieval.service import search as retrieval_search

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = "I don't have enough information in the ingested documents to answer this question."
GREETING_ANSWER = "Hello! Ask me a question about your uploaded documents and I'll do my best to help."

# ERP-046: a plain greeting/pleasantry with no other content should never reach retrieval or
# the LLM -- top_k retrieval always returns *something*, regardless of relevance, and a model
# asked to answer "hi" from irrelevant context has nothing good to do with it (observed live:
# one model paraphrased its own system prompt back as if it were an answer). Anchored on both
# ends (^...$) so it only matches a message that IS a greeting, not one that merely starts with
# one ("hi, what does section 3 say about pricing?" must not match).
_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|hiya|yo|howdy|greetings|good\s*(morning|afternoon|evening)"
    r"|how\s*are\s*you|what'?s\s*up)(\s*(there|folks|team|guys|all))?[\s!.,?]*$",
    re.IGNORECASE,
)


class ConversationAccessDeniedError(Exception):
    """Raised when a client-supplied `conversation_id` already exists but belongs to a different owner."""


_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")


def _cited_chunks(answer: str, included_chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Return only the `included_chunks` the answer actually cites with a `[n]` marker.

    `included_chunks` is 1-indexed by prompt position (`build_prompt`'s `[1]`, `[2]`, ...);
    a marker number with no corresponding chunk (a hallucinated citation) is silently
    ignored, matching the existing tolerance for LLM output that doesn't perfectly follow
    instructions. Preserves `included_chunks`' original order (ERP-055) -- a chunk the model
    never referenced is not returned, even though it was present in its context window.
    """
    cited_indices = {int(match) for match in _CITATION_MARKER_RE.findall(answer)}
    return [
        chunk
        for index, chunk in enumerate(included_chunks, start=1)
        if index in cited_indices
    ]


def _citations_for(chunks: list[RetrievedChunk], reranked: bool) -> list[Citation]:
    return [
        Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_filename=chunk.source_filename,
            score=chunk.score,
            reranked=reranked,
        )
        for chunk in chunks
    ]


def generate(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResponse:
    """Retrieve context for `query` (scoped to `owner_id`) and synthesize a grounded, citation-marked answer.

    `conversation_id` is a stateless/stateful switch. `None` (the default) is fully
    stateless: no session opened, no history loaded, no rewriting, nothing persisted --
    behavior is identical to the single-turn-only version of this function. Given a
    `conversation_id`, the last `settings.history_window_turns` messages are loaded (empty
    on a conversation's first turn) via a short-lived read session that is closed before
    rewriting/retrieval/generation run; no DB session is held open across those LLM calls.
    If history exists, `query` is rewritten into a standalone retrieval query via
    `rewrite_query` before running retrieval, and that history is rendered into the
    generation prompt. Both the raw user turn and the assistant's answer are persisted
    together in one transaction via a second, separately opened write session, but only
    after generation succeeds -- a failure commits nothing (the write session isn't even
    opened until `answer`/`citations` are fully computed). A conversation created here is
    owned by `owner_id`; if `conversation_id` already exists and belongs to a different
    owner, raises `ConversationAccessDeniedError` before any history is read, retrieval
    runs, or the LLM is called -- the router maps this to a 404.

    Runs the existing hybrid retrieval pipeline unmodified (`rerank`/`expand_sections`
    passed straight through). If retrieval returns no chunks, short-circuits to
    `NO_CONTEXT_ANSWER` without constructing or calling an `LLMClient` for the final answer
    (a conversational short-circuit still persists both turns, so the conversation record
    reflects that the question went unanswered).

    `settings`/`llm_client` are injectable for testing; default to the process-wide
    cached `GenerationSettings` and an `OllamaLLMClient` built from it.
    """
    settings = settings or get_generation_settings()

    if conversation_id is None:
        if _GREETING_RE.match(query):
            return GenerationResponse(answer=GREETING_ANSWER, citations=[], conversation_id=None)

        chunks = retrieval_search(query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections)
        if not chunks:
            return GenerationResponse(answer=NO_CONTEXT_ANSWER, citations=[], conversation_id=None)

        llm_client = llm_client or OllamaLLMClient(settings)
        user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        citations = _citations_for(_cited_chunks(answer, included_chunks), reranked=rerank)
        return GenerationResponse(answer=answer, citations=citations, conversation_id=None)

    session_factory = get_session_factory()

    with session_factory() as read_session:
        existing_owner_id = get_conversation_owner_id(read_session, conversation_id)
        if existing_owner_id is not None and existing_owner_id != owner_id:
            raise ConversationAccessDeniedError
        history_records = get_recent_messages(
            read_session, conversation_id, settings.history_window_turns
        )
        history = [ConversationTurn(role=r.role, content=r.content) for r in history_records]

    if _GREETING_RE.match(query):
        answer = GREETING_ANSWER
        citations = []
    else:
        if history:
            llm_client = llm_client or OllamaLLMClient(settings)
            rewritten_query = rewrite_query(query, history, llm_client)
        else:
            rewritten_query = query

        chunks = retrieval_search(
            rewritten_query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections
        )
        if not chunks:
            answer = NO_CONTEXT_ANSWER
            citations = []
        else:
            llm_client = llm_client or OllamaLLMClient(settings)
            user_prompt, included_chunks = build_prompt(
                query, chunks, settings.max_context_chars, history=history
            )
            answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
            citations = _citations_for(_cited_chunks(answer, included_chunks), reranked=rerank)

    with session_factory() as write_session:
        get_or_create_conversation(write_session, conversation_id, owner_id)
        append_message(write_session, conversation_id, "user", query)
        append_message(write_session, conversation_id, "assistant", answer)
        write_session.commit()

    return GenerationResponse(answer=answer, citations=citations, conversation_id=conversation_id)


def generate_stream(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Streaming counterpart to `generate`: yields `(event, data)` tuples instead of returning one response.

    Event sequence on success: zero or more `("token", {"text": "..."})` (one per chunk of
    generated text), then one `("citations", {"citations": [...]})`, then a terminal
    `("done", {"conversation_id": str | None})`. Citations are emitted after the tokens, not
    before (ERP-055) -- which chunks were actually cited can only be known once the full
    answer text exists, since only chunks referenced by a `[n]` marker in that text are
    included (see `_cited_chunks`). On any failure, yields a terminal `("error", {"detail":
    "..."})` instead of `"done"` -- callers must treat `"error"` as the end of the stream,
    not attempt to resume iteration.

    Shares `generate()`'s stateless/stateful branching, rewrite, ownership, and persistence
    semantics exactly (see `generate`'s docstring) -- only the delivery mechanism differs.
    Persistence for a stateful request happens only after the full answer is assembled,
    immediately before the `"done"` event, so a client disconnect (which raises
    `GeneratorExit` at the suspended `yield`) or a mid-generation exception both skip it,
    leaving conversation history exactly as it was before the request.
    """
    try:
        settings = settings or get_generation_settings()
        if conversation_id is None:
            if _GREETING_RE.match(query):
                yield "token", {"text": GREETING_ANSWER}
                yield "citations", {"citations": []}
                yield "done", {"conversation_id": None}
                return

            chunks = retrieval_search(
                query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections
            )
            if not chunks:
                yield "token", {"text": NO_CONTEXT_ANSWER}
                yield "citations", {"citations": []}
                yield "done", {"conversation_id": None}
                return

            llm_client = llm_client or OllamaLLMClient(settings)
            user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
            answer_parts: list[str] = []
            for piece in llm_client.generate_stream(SYSTEM_PROMPT, user_prompt):
                answer_parts.append(piece)
                yield "token", {"text": piece}
            answer = "".join(answer_parts)
            citations = _citations_for(_cited_chunks(answer, included_chunks), reranked=rerank)
            yield "citations", {"citations": [c.model_dump() for c in citations]}
            yield "done", {"conversation_id": None}
            return

        session_factory = get_session_factory()
        with session_factory() as read_session:
            existing_owner_id = get_conversation_owner_id(read_session, conversation_id)
            if existing_owner_id is not None and existing_owner_id != owner_id:
                raise ConversationAccessDeniedError
            history_records = get_recent_messages(
                read_session, conversation_id, settings.history_window_turns
            )
            history = [ConversationTurn(role=r.role, content=r.content) for r in history_records]

        if _GREETING_RE.match(query):
            yield "token", {"text": GREETING_ANSWER}
            yield "citations", {"citations": []}
            answer = GREETING_ANSWER
        else:
            if history:
                llm_client = llm_client or OllamaLLMClient(settings)
                rewritten_query = rewrite_query(query, history, llm_client)
            else:
                rewritten_query = query

            chunks = retrieval_search(
                rewritten_query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections
            )
            if not chunks:
                yield "token", {"text": NO_CONTEXT_ANSWER}
                yield "citations", {"citations": []}
                answer = NO_CONTEXT_ANSWER
            else:
                llm_client = llm_client or OllamaLLMClient(settings)
                user_prompt, included_chunks = build_prompt(
                    query, chunks, settings.max_context_chars, history=history
                )
                answer_parts = []
                for piece in llm_client.generate_stream(SYSTEM_PROMPT, user_prompt):
                    answer_parts.append(piece)
                    yield "token", {"text": piece}
                answer = "".join(answer_parts)
                citations = _citations_for(_cited_chunks(answer, included_chunks), reranked=rerank)
                yield "citations", {"citations": [c.model_dump() for c in citations]}

        with session_factory() as write_session:
            get_or_create_conversation(write_session, conversation_id, owner_id)
            append_message(write_session, conversation_id, "user", query)
            append_message(write_session, conversation_id, "assistant", answer)
            write_session.commit()

        yield "done", {"conversation_id": str(conversation_id)}
    except Exception:
        logger.exception("Streaming generation failed")
        yield "error", {"detail": "Generation query failed"}


def get_conversation_history(
    conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> ConversationHistoryResponse | None:
    """Return every message in `conversation_id`, oldest first.

    Returns `None` if the conversation doesn't exist, or if it belongs to a different
    `owner_id` -- the caller (router) maps both to a 404.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        if get_conversation(session, conversation_id, owner_id) is None:
            return None
        records = get_all_messages(session, conversation_id)

    messages = [
        Message(role=record.role, content=record.content, created_at=record.created_at)
        for record in records
    ]
    return ConversationHistoryResponse(conversation_id=conversation_id, messages=messages)


def list_conversations(owner_id: uuid.UUID) -> ConversationListResponse:
    """Return `owner_id`'s conversations, newest first, each with a preview of its first message.

    This is the fix for conversation history not surviving a login on a new browser/device:
    full history was always persisted server-side (see `get_conversation_history`), but there
    was previously no way to enumerate a caller's conversations at all -- the frontend sidebar
    relied solely on a client-side cache that a fresh browser/device never had.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        conversations = list_conversations_for_owner(session, owner_id)
        previews = get_first_user_messages(session, [c.id for c in conversations])

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                conversation_id=c.id,
                created_at=c.created_at,
                preview=previews.get(c.id),
                title=c.title,
            )
            for c in conversations
        ]
    )


def rename_conversation(conversation_id: uuid.UUID, owner_id: uuid.UUID, title: str) -> bool:
    """Set `conversation_id`'s explicit title. Returns `False` if unknown or not owned by `owner_id`."""
    session_factory = get_session_factory()
    with session_factory() as session:
        renamed = repository_rename_conversation(session, conversation_id, owner_id, title)
        if renamed:
            session.commit()
    return renamed
