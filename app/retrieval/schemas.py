"""Pydantic schemas for the retrieval API's request and response."""

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    """A semantic search request."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    rerank: bool = Field(default=False)
    expand_sections: bool = Field(default=False)
    document_ids: list[str] | None = Field(
        default=None,
        description=(
            "Restrict the search to these document IDs (ERP-044). Omitted/null searches "
            "everything the caller owns, unchanged from before this field existed; an empty "
            "list explicitly searches nothing and returns no results."
        ),
    )


class RetrievedChunk(BaseModel):
    """A single retrieved chunk, with full provenance metadata and a fused relevance score."""

    chunk_id: str
    document_id: str
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str
    score: float = Field(
        description=(
            "Normalized reciprocal-rank-fusion score in (0, 1], not a raw similarity/distance "
            "metric. 1.0 means the chunk ranked first in every retriever that found it."
        )
    )


class RetrievalResponse(BaseModel):
    """Ranked results of a semantic search query, most relevant first."""

    results: list[RetrievedChunk]
