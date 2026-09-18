"""Persistence for multi-turn conversations."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.generation.models import ConversationMessageRecord, ConversationRecord


def get_or_create_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> ConversationRecord:
    """Return `conversation_id`'s `ConversationRecord`, creating it (owned by `owner_id`) if new.

    Does not commit -- the caller controls the transaction boundary.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    if conversation is None:
        conversation = ConversationRecord(id=conversation_id, owner_id=owner_id)
        session.add(conversation)
        session.flush()
    return conversation


def rename_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: uuid.UUID, title: str
) -> bool:
    """Set `conversation_id`'s explicit title if it exists and is owned by `owner_id`.

    Returns `False` (does nothing) if the conversation doesn't exist or belongs to a
    different owner, matching the existing ownership-check convention. Does not commit --
    the caller controls the transaction boundary.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    if conversation is None or conversation.owner_id != owner_id:
        return False
    conversation.title = title
    return True


def get_conversation_owner_id(session: Session, conversation_id: uuid.UUID) -> uuid.UUID | None:
    """Return the owner_id of `conversation_id` if it already exists, else `None`.

    Does not filter by owner -- this is a raw existence+ownership lookup, used to detect
    (before doing any work) whether a client-supplied conversation_id belongs to someone
    else, distinct from `get_conversation`'s "does it belong to owner_id" check.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    return None if conversation is None else conversation.owner_id


def append_message(
    session: Session, conversation_id: uuid.UUID, role: str, content: str
) -> ConversationMessageRecord:
    """Append one message to `conversation_id`. Does not commit."""
    message = ConversationMessageRecord(
        id=uuid.uuid4(), conversation_id=conversation_id, role=role, content=content
    )
    session.add(message)
    session.flush()
    return message


def get_recent_messages(
    session: Session, conversation_id: uuid.UUID, limit: int
) -> list[ConversationMessageRecord]:
    """Return up to `limit` most recent messages for `conversation_id`, oldest first.

    `[]` if the conversation doesn't exist or has no messages yet.
    """
    rows = session.scalars(
        select(ConversationMessageRecord)
        .where(ConversationMessageRecord.conversation_id == conversation_id)
        .order_by(ConversationMessageRecord.sequence.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def get_conversation(
    session: Session, conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> ConversationRecord | None:
    """Return `conversation_id`'s `ConversationRecord` if it exists and belongs to `owner_id`.

    Returns `None` both when the conversation doesn't exist and when it belongs to a
    different owner -- callers can't distinguish the two, matching the ingestion job
    404 convention. Unlike `get_or_create_conversation`, never creates a row.
    """
    conversation = session.get(ConversationRecord, conversation_id)
    if conversation is None or conversation.owner_id != owner_id:
        return None
    return conversation


def get_all_messages(session: Session, conversation_id: uuid.UUID) -> list[ConversationMessageRecord]:
    """Return every message for `conversation_id`, oldest first. `[]` if none exist."""
    return list(
        session.scalars(
            select(ConversationMessageRecord)
            .where(ConversationMessageRecord.conversation_id == conversation_id)
            .order_by(ConversationMessageRecord.sequence.asc())
        ).all()
    )


def list_conversations_for_owner(session: Session, owner_id: uuid.UUID) -> list[ConversationRecord]:
    """Return `owner_id`'s conversations, newest first."""
    return list(
        session.scalars(
            select(ConversationRecord)
            .where(ConversationRecord.owner_id == owner_id)
            .order_by(ConversationRecord.created_at.desc())
        ).all()
    )


def get_first_user_messages(
    session: Session, conversation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Return each conversation's first user-role message content, keyed by conversation_id.

    Two queries rather than N+1: a subquery finds each conversation's minimum `sequence`
    among its user messages, then one join fetches those rows' content. A conversation with
    no user message yet is simply absent from the result. `{}` for empty input.
    """
    if not conversation_ids:
        return {}
    first_sequence = (
        select(
            ConversationMessageRecord.conversation_id,
            func.min(ConversationMessageRecord.sequence).label("first_sequence"),
        )
        .where(
            ConversationMessageRecord.conversation_id.in_(conversation_ids),
            ConversationMessageRecord.role == "user",
        )
        .group_by(ConversationMessageRecord.conversation_id)
        .subquery()
    )
    rows = session.execute(
        select(ConversationMessageRecord.conversation_id, ConversationMessageRecord.content).join(
            first_sequence,
            (ConversationMessageRecord.conversation_id == first_sequence.c.conversation_id)
            & (ConversationMessageRecord.sequence == first_sequence.c.first_sequence),
        )
    ).all()
    return {row.conversation_id: row.content for row in rows}
