"""Grounded answer generation over hybrid-retrieved chunks, with optional multi-turn memory."""

import contextvars
import logging
import queue
import re
import threading
import uuid
from typing import Any, Iterator

from app.core.db import get_session_factory
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.repository import (
    append_message,
    clear_message_feedback,
    get_all_messages,
    get_conversation,
    get_conversation_owner_id,
    get_feedback_for_messages,
    get_first_user_messages,
    get_or_create_conversation,
    get_recent_messages,
    list_conversations_for_owner,
    set_message_feedback,
    title_exists_for_owner,
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
# ERP-096: persisted in place of an assistant reply when background generation fails after the
# user's turn was already saved -- so a caller who reconnects later (after a refresh or a
# same-page remount, per ERP-096's investigation) sees a resolved conversation instead of a
# question that looks permanently unanswered.
FAILURE_NOTICE = "Something went wrong while generating an answer. Please try asking again."

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


class ConversationTitleConflictError(Exception):
    """Raised when a rename would collide with another of the same owner's conversation titles."""


# Matches both "[1]" and "[1, 2, 5]" -- a model asked to cite [1], [2], etc. sometimes bundles
# several into one bracket instead of writing separate markers (ERP-065). Each match's captured
# group is itself comma-split in `_cited_chunks` below.
_CITATION_MARKER_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _cited_chunks(answer: str, included_chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Return only the `included_chunks` the answer actually cites with a `[n]` marker.

    `included_chunks` is 1-indexed by prompt position (`build_prompt`'s `[1]`, `[2]`, ...);
    a marker number with no corresponding chunk (a hallucinated citation) is silently
    ignored, matching the existing tolerance for LLM output that doesn't perfectly follow
    instructions. Preserves `included_chunks`' original order (ERP-055) -- a chunk the model
    never referenced is not returned, even though it was present in its context window.
    """
    cited_indices: set[int] = set()
    for match in _CITATION_MARKER_RE.findall(answer):
        cited_indices.update(int(piece) for piece in match.split(","))
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


def warmup_llm(llm_client: OllamaLLMClient | None = None) -> None:
    """Best-effort ping to wake a scale-to-zero LLM backend before a real request needs it.

    (ERP-091). Never raises -- a failed/slow warmup just means the first real request pays the
    full cold-start cost, exactly as it would have without this call, so a caller can fire this
    and ignore the outcome entirely.
    """
    llm_client = llm_client or OllamaLLMClient(get_generation_settings())
    try:
        llm_client.ping()
    except Exception:
        logger.warning(
            "LLM warmup ping failed -- first real request will pay the full cold-start cost",
            exc_info=True,
        )


def generate(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
    document_ids: list[str] | None = None,
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

        chunks = retrieval_search(
            query, top_k, owner_id, rerank=rerank, expand_sections=expand_sections, document_ids=document_ids
        )
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
            rewritten_query,
            top_k,
            owner_id,
            rerank=rerank,
            expand_sections=expand_sections,
            document_ids=document_ids,
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
        assistant_record = append_message(
            write_session,
            conversation_id,
            "assistant",
            answer,
            citations=[c.model_dump() for c in citations],
        )
        write_session.commit()

    return GenerationResponse(
        answer=answer,
        citations=citations,
        conversation_id=conversation_id,
        assistant_message_id=assistant_record.id,
    )


def generate_stream(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
    document_ids: list[str] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Streaming counterpart to `generate`: yields `(event, data)` tuples instead of returning one response.

    Event sequence on success: zero or more `("token", {"text": "..."})` (one per chunk of
    generated text), then one `("citations", {"citations": [...]})`, then a terminal `("done",
    {"conversation_id": str | None})` -- for a stateful request, that payload also carries
    `"assistant_message_id"` (the persisted assistant turn's ID, ERP-045), omitted for a
    stateless one since nothing is persisted. Citations are emitted after the tokens, not
    before (ERP-055) -- which chunks were actually cited can only be known once the full
    answer text exists, since only chunks referenced by a `[n]` marker in that text are
    included (see `_cited_chunks`). On any failure, yields a terminal `("error", {"detail":
    "..."})` instead of `"done"` -- callers must treat `"error"` as the end of the stream,
    not attempt to resume iteration.

    Shares `generate()`'s stateless/stateful branching, rewrite, and ownership semantics
    exactly (see `generate`'s docstring) -- only the delivery mechanism, and (for a stateful
    request) the persistence timing, differ. See `_run_stateful_generation`'s docstring for why
    persistence is decoupled from this generator's own lifecycle (ERP-096).
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
                query,
                top_k,
                owner_id,
                rerank=rerank,
                expand_sections=expand_sections,
                document_ids=document_ids,
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

        # ERP-096: persist the user's turn immediately, before generation even starts -- not
        # bundled with the assistant's reply at the end. A page refresh, a same-page remount
        # (e.g. a backgrounded tab getting silently reloaded), or any other disconnect must
        # never erase the question itself, only (at worst) delay the answer.
        with session_factory() as write_session:
            get_or_create_conversation(write_session, conversation_id, owner_id)
            append_message(write_session, conversation_id, "user", query)
            write_session.commit()

        event_queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        # Propagates the current OTel trace context (a `contextvars.ContextVar` under the hood)
        # into the thread -- otherwise every span `_run_stateful_generation` creates (via
        # retrieval/rewrite/the LLM call) would start a disconnected new root trace instead of
        # nesting under this request's own trace, since a plain `threading.Thread` starts with
        # a fresh context by default.
        worker = threading.Thread(
            target=contextvars.copy_context().run,
            args=(
                _run_stateful_generation,
                query,
                top_k,
                owner_id,
                rerank,
                expand_sections,
                conversation_id,
                settings,
                llm_client,
                document_ids,
                history,
                event_queue,
            ),
            daemon=True,
        )
        worker.start()

        while True:
            item = event_queue.get()
            if item is None:
                return
            yield item
    except Exception:
        logger.exception("Streaming generation failed")
        yield "error", {"detail": "Generation query failed"}


def _run_stateful_generation(
    query: str,
    top_k: int,
    owner_id: uuid.UUID,
    rerank: bool,
    expand_sections: bool,
    conversation_id: uuid.UUID,
    settings: GenerationSettings,
    llm_client: LLMClient | None,
    document_ids: list[str] | None,
    history: list[ConversationTurn],
    event_queue: "queue.Queue[tuple[str, dict[str, Any]] | None]",
) -> None:
    """Runs rewrite/retrieval/generation and persists the assistant's reply.

    Independent of whether `generate_stream`'s caller is still connected (ERP-096) --
    `generate_stream` only relays whatever this puts on `event_queue` to a live SSE client --
    it does not drive this work itself. Run in a background thread (not an asyncio task,
    since `LLMClient.generate_stream` and the retrieval/rewrite calls are synchronous/blocking)
    so a client disconnect, which only stops `generate_stream` from being iterated further, can
    never cut this off mid-generation. Always terminates the queue with a `None` sentinel so
    `generate_stream`'s relay loop knows to stop.

    On failure, persists a neutral assistant-role notice rather than leaving the user's
    already-persisted question dangling with no reply forever -- both for a live client (which
    also gets the `"error"` event directly) and for a caller who reconnects later expecting
    *something* to have resolved the question it already sees in history.
    """
    session_factory = get_session_factory()
    try:
        citations: list[Citation] = []
        if _GREETING_RE.match(query):
            event_queue.put(("token", {"text": GREETING_ANSWER}))
            event_queue.put(("citations", {"citations": []}))
            answer = GREETING_ANSWER
        else:
            if history:
                llm_client = llm_client or OllamaLLMClient(settings)
                rewritten_query = rewrite_query(query, history, llm_client)
            else:
                rewritten_query = query

            chunks = retrieval_search(
                rewritten_query,
                top_k,
                owner_id,
                rerank=rerank,
                expand_sections=expand_sections,
                document_ids=document_ids,
            )
            if not chunks:
                event_queue.put(("token", {"text": NO_CONTEXT_ANSWER}))
                event_queue.put(("citations", {"citations": []}))
                answer = NO_CONTEXT_ANSWER
            else:
                llm_client = llm_client or OllamaLLMClient(settings)
                user_prompt, included_chunks = build_prompt(
                    query, chunks, settings.max_context_chars, history=history
                )
                answer_parts: list[str] = []
                for piece in llm_client.generate_stream(SYSTEM_PROMPT, user_prompt):
                    answer_parts.append(piece)
                    event_queue.put(("token", {"text": piece}))
                answer = "".join(answer_parts)
                citations = _citations_for(_cited_chunks(answer, included_chunks), reranked=rerank)
                event_queue.put(("citations", {"citations": [c.model_dump() for c in citations]}))

        with session_factory() as write_session:
            assistant_record = append_message(
                write_session,
                conversation_id,
                "assistant",
                answer,
                citations=[c.model_dump() for c in citations],
            )
            write_session.commit()

        event_queue.put(
            (
                "done",
                {
                    "conversation_id": str(conversation_id),
                    "assistant_message_id": str(assistant_record.id),
                },
            )
        )
    except Exception:
        logger.exception("Streaming generation failed (background)")
        try:
            with session_factory() as write_session:
                append_message(write_session, conversation_id, "assistant", FAILURE_NOTICE)
                write_session.commit()
        except Exception:
            logger.exception("Failed to persist the failure notice itself")
        event_queue.put(("error", {"detail": "Generation query failed"}))
    finally:
        event_queue.put(None)


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
        feedback_by_message_id = get_feedback_for_messages(session, [r.id for r in records])

    messages = [
        Message(
            id=record.id,
            role=record.role,
            content=record.content,
            created_at=record.created_at,
            feedback=feedback_by_message_id.get(record.id),
            citations=[Citation(**c) for c in (record.citations or [])],
        )
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
    """Set `conversation_id`'s explicit title. Returns `False` if unknown or not owned by `owner_id`.

    Raises `ConversationTitleConflictError` (before touching anything) if `owner_id` already has
    a different conversation with the same title, case-insensitively (ERP-062) -- renaming a
    conversation to its own current title is not a conflict.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        if title_exists_for_owner(session, owner_id, title, exclude_conversation_id=conversation_id):
            raise ConversationTitleConflictError
        renamed = repository_rename_conversation(session, conversation_id, owner_id, title)
        if renamed:
            session.commit()
    return renamed


def set_feedback(message_id: uuid.UUID, owner_id: uuid.UUID, rating: str) -> bool:
    """Set (upsert) `owner_id`'s feedback for `message_id`.

    Returns `False` if the message doesn't exist or isn't owned (via its conversation) by
    `owner_id` -- the router maps this to a 404.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        set_result = set_message_feedback(session, message_id, owner_id, rating)
        if set_result:
            session.commit()
    return set_result


def clear_feedback(message_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
    """Clear `owner_id`'s feedback for `message_id`, if any.

    Returns `False` if the message doesn't exist or isn't owned by `owner_id`; `True` (a
    no-op) if it's owned but has no feedback to clear.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        cleared = clear_message_feedback(session, message_id, owner_id)
        if cleared:
            session.commit()
    return cleared
