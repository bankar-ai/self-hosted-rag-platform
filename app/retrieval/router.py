"""Retrieval API: semantic search over ingested chunks."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.retrieval.schemas import RetrievalQuery, RetrievalResponse
from app.retrieval.service import search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/query")
def query(
    query_request: RetrievalQuery, current_user: CurrentUser = Depends(get_current_user)
) -> RetrievalResponse:
    """Run a semantic search query, restricted to the caller's own documents."""
    try:
        results = search(
            query_request.query,
            query_request.top_k,
            current_user.id,
            rerank=query_request.rerank,
            expand_sections=query_request.expand_sections,
            document_ids=query_request.document_ids,
        )
    except Exception as exc:
        logger.exception("Retrieval query failed")
        raise HTTPException(status_code=503, detail="Retrieval query failed") from exc
    return RetrievalResponse(results=results)
