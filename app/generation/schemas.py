"""Pydantic schemas for the generation API's request and response."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GenerationQuery(BaseModel):
    """A grounded-answer generation request.

    `conversation_id` is a stateless/stateful switch: omitted (`None`) keeps this request
    fully stateless -- no history loaded, nothing persisted, matching the original
    single-turn behavior exactly. Provided, it is a client-supplied UUID: if no
    conversation with that ID exists yet, one is created; if it does, it is continued.
    """

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = Field(default=False)
    expand_sections: bool = Field(default=False)
    conversation_id: uuid.UUID | None = Field(default=None)
    document_ids: list[str] | None = Field(
        default=None,
        description=(
            "Restrict retrieval to these document IDs (ERP-044). Omitted/null searches "
            "everything the caller owns, unchanged from before this field existed; an empty "
            "list explicitly searches nothing, short-circuiting to the same 'not enough "
            "information' answer as a genuinely empty retrieval result."
        ),
    )


class ConversationTurn(BaseModel):
    """One turn of conversation history, decoupled from how it's persisted."""

    role: str
    content: str


class Citation(BaseModel):
    """Provenance for one chunk the answer actually cited with a `[n]` marker.

    Only chunks referenced by an inline `[n]` marker in the answer text are included --
    a chunk merely present in the LLM's context window but never cited is not (ERP-055).
    """

    chunk_id: str
    document_id: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str
    score: float = Field(
        description=(
            "RetrievedChunk.score, unchanged: a fused RRF score (bounded to (0, 1]) unless "
            "`reranked` is true, in which case it is the reranker's score instead -- the two "
            "are not on a comparable scale (ERP-056)."
        )
    )
    reranked: bool = Field(
        description="Whether this query used rerank=True -- tells the caller how to interpret `score`."
    )


class GenerationResponse(BaseModel):
    """A synthesized answer with the citations backing its inline [n] markers.

    `conversation_id` is `None` for a stateless request, otherwise the conversation's ID
    (echoed back, or newly created on this call). `assistant_message_id` is likewise `None`
    for a stateless request (nothing is persisted); otherwise it's the persisted assistant
    turn's ID, so the caller can attach feedback (ERP-045) to it without a separate fetch.
    """

    answer: str
    citations: list[Citation]
    conversation_id: uuid.UUID | None = None
    assistant_message_id: uuid.UUID | None = None


class Message(BaseModel):
    """One persisted turn in a conversation's history, with when it was recorded.

    `id` and `feedback` (ERP-045) let the caller show/set a thumbs up/down rating -- `feedback`
    is `None` both when the message has never been rated and (always) for a `"user"`-role
    message, which can't be rated at all. `citations` (ERP-080) is likewise always `[]` for a
    `"user"`-role message and for any message persisted before this field existed --  it lets
    a reloaded conversation's assistant messages keep working, clickable citation markers.
    """

    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    feedback: Literal["up", "down"] | None = None
    citations: list[Citation] = Field(default_factory=list)


class ConversationHistoryResponse(BaseModel):
    """The full message history of one conversation, oldest first."""

    conversation_id: uuid.UUID
    messages: list[Message]


class ConversationSummary(BaseModel):
    """One of the caller's conversations, with a preview of its first message.

    `title` is the explicit user-set name (ERP-061), `None` if never renamed -- callers should
    fall back to `preview` in that case. `preview` itself is always the first-message text,
    regardless of whether `title` is set.
    """

    conversation_id: uuid.UUID
    created_at: datetime
    preview: str | None = None
    title: str | None = None


class RenameConversationRequest(BaseModel):
    """A request to set a conversation's explicit display title."""

    title: str = Field(min_length=1, max_length=200)


class ConversationListResponse(BaseModel):
    """The caller's conversations, newest first."""

    conversations: list[ConversationSummary]


class SetMessageFeedbackRequest(BaseModel):
    """A request to rate one assistant message (ERP-045)."""

    rating: Literal["up", "down"]
