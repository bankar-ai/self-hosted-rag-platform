"""SQLAlchemy ORM models for persisted documents and chunks."""

import uuid
from datetime import datetime

from sqlalchemy import DDL, JSON, ForeignKey, Identity, Index, Text, event
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all ingestion ORM models."""


class DocumentRecord(Base):
    """A single ingested document."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(primary_key=True)
    filename: Mapped[str]
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # ERP-076: "high" for a document the fast path parsed cleanly (matches
    # app.ingestion.parsers.FAST_PATH_CONFIDENCE), else docling's own document-level
    # confidence grade for one that needed the OCR fallback. Backfilled to "high" for any
    # document ingested before this column existed.
    parsing_confidence: Mapped[str] = mapped_column(server_default="high")


class ChunkRecord(Base):
    """A single persisted chunk of a document, with full provenance metadata.

    `vector_id` is a separate autoincrement integer identity, distinct from the
    business-facing string `chunk_id` — FAISS requires int64 vector IDs.
    """

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    section_path: Mapped[list[str]] = mapped_column(JSON)
    page_start: Mapped[int]
    page_end: Mapped[int]
    char_count: Mapped[int]
    parser_used: Mapped[str]
    source_filename: Mapped[str]
    vector_id: Mapped[int] = mapped_column(Identity(always=True), unique=True)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)

    __table_args__ = (Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),)


# `search_vector` is kept in sync by a Postgres trigger rather than `Computed(...)` (see
# alembic/versions/ec9863a88014_*.py for why) so `Base.metadata.create_all` -- used by the test
# suite -- must create the same trigger to reproduce production's auto-populate behavior.
event.listen(
    ChunkRecord.__table__,
    "after_create",
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE OR REPLACE FUNCTION chunks_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', NEW.text);
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    ),
)
event.listen(
    ChunkRecord.__table__,
    "after_create",
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER chunks_search_vector_trigger
        BEFORE INSERT OR UPDATE OF text ON chunks
        FOR EACH ROW EXECUTE FUNCTION chunks_search_vector_update();
        """
    ),
)
