"""Generation API: grounded answer synthesis over retrieved chunks."""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.generation.schemas import (
    ConversationHistoryResponse,
    ConversationListResponse,
    GenerationQuery,
    GenerationResponse,
    RenameConversationRequest,
    SetMessageFeedbackRequest,
)
from app.generation.service import (
    ConversationAccessDeniedError,
    ConversationTitleConflictError,
    clear_feedback,
    generate,
    generate_stream,
    get_conversation_history,
    list_conversations,
    rename_conversation,
    set_feedback,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/query")
def query(
    query_request: GenerationQuery, current_user: CurrentUser = Depends(get_current_user)
) -> GenerationResponse:
    """Run retrieval + LLM synthesis and return a grounded, cited answer."""
    try:
        return generate(
            query_request.query,
            query_request.top_k,
            current_user.id,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
            conversation_id=query_request.conversation_id,
        )
    except ConversationAccessDeniedError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except Exception as exc:
        logger.exception("Generation query failed")
        raise HTTPException(status_code=503, detail="Generation query failed") from exc


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _event_stream(query_request: GenerationQuery, owner_id: uuid.UUID) -> Iterator[str]:
    for event, data in generate_stream(
        query_request.query,
        query_request.top_k,
        owner_id,
        rerank=query_request.rerank,
        expand_sections=query_request.expand_sections,
        conversation_id=query_request.conversation_id,
    ):
        yield _format_sse(event, data)


@router.post("/query/stream")
def query_stream(
    query_request: GenerationQuery, current_user: CurrentUser = Depends(get_current_user)
) -> StreamingResponse:
    """Run retrieval + LLM synthesis, streaming the answer as Server-Sent Events.

    Unlike `POST /generation/query`, failures surface as a terminal `error` SSE event
    (status stays 200, since headers are already sent once streaming starts) rather than
    an HTTP error status -- see `generate_stream`'s docstring.
    """
    return StreamingResponse(
        _event_stream(query_request, current_user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@conversations_router.get("")
def list_conversations_endpoint(
    current_user: CurrentUser = Depends(get_current_user),
) -> ConversationListResponse:
    """Return the caller's conversations, newest first."""
    return list_conversations(current_user.id)


@conversations_router.get("/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> ConversationHistoryResponse:
    """Return a conversation's full message history, oldest first, or 404 if unknown or not yours."""
    history = get_conversation_history(conversation_id, current_user.id)
    if history is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return history


@conversations_router.patch("/{conversation_id}", status_code=204)
def rename_conversation_endpoint(
    conversation_id: uuid.UUID,
    rename_request: RenameConversationRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Set a conversation's explicit display title.

    404 if unknown or not owned by the caller; 409 if the caller already has another
    conversation with the same title (case-insensitive, ERP-062).
    """
    try:
        renamed = rename_conversation(conversation_id, current_user.id, rename_request.title)
    except ConversationTitleConflictError as exc:
        raise HTTPException(status_code=409, detail="A conversation with that name already exists") from exc
    if not renamed:
        raise HTTPException(status_code=404, detail="Conversation not found")


@conversations_router.put("/messages/{message_id}/feedback", status_code=204)
def set_message_feedback_endpoint(
    message_id: uuid.UUID,
    feedback_request: SetMessageFeedbackRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    """Rate a message up or down. 404 if unknown or not owned (via its conversation) by the caller."""
    if not set_feedback(message_id, current_user.id, feedback_request.rating):
        raise HTTPException(status_code=404, detail="Message not found")


@conversations_router.delete("/messages/{message_id}/feedback", status_code=204)
def clear_message_feedback_endpoint(
    message_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> None:
    """Clear the caller's feedback for a message, if any. 404 if unknown or not owned."""
    if not clear_feedback(message_id, current_user.id):
        raise HTTPException(status_code=404, detail="Message not found")
