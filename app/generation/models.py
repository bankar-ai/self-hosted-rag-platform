"""SQLAlchemy ORM models for multi-turn conversation memory (ERP-018)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Identity, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.ingestion.models import Base


class ConversationRecord(Base):
    """A single multi-turn conversation. `id` is always client-supplied, never generated here."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    title: Mapped[str | None] = mapped_column(Text, default=None)


class ConversationMessageRecord(Base):
    """A single turn (`role` is `"user"` or `"assistant"`) within a conversation."""

    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sequence: Mapped[int] = mapped_column(Identity(always=True), unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MessageFeedbackRecord(Base):
    """A viewer's thumbs up/down rating of one assistant message (ERP-045).

    One row per `message_id` (upserted in place by `set_message_feedback`, not a new row per
    rating change) -- `rating` is `"up"` or `"down"`. Access is never checked against this
    table's own `owner_id` directly; every read/write path verifies the message belongs to a
    conversation owned by the caller first, via a join, so a viewer can only ever rate their
    own messages.
    """

    __tablename__ = "message_feedback"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation_messages.id"), primary_key=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    rating: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
