"""Ingestion API: PDF upload (async job) and job-status polling."""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.embedding.service import delete_document_and_vectors
from app.ingestion import jobs
from app.ingestion.config import get_settings
from app.ingestion.schemas import (
    ChunkDetailResponse,
    DocumentListResponse,
    JobListResponse,
    JobStatusResponse,
    JobSummary,
)
from app.ingestion.service import get_chunk_detail, list_documents

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
documents_router = APIRouter(prefix="/documents", tags=["documents"])

_PDF_MAGIC = b"%PDF-"
_COPY_CHUNK_SIZE = 1024 * 1024


@router.post("/pdf", status_code=status.HTTP_202_ACCEPTED)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    """Validate and stream an uploaded PDF to disk, then schedule an async ingestion job."""
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF (content-type application/pdf)")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    header = await file.read(5)
    if header != _PDF_MAGIC:
        raise HTTPException(status_code=400, detail="File is not a valid PDF (missing %PDF- header)")
    await file.seek(0)

    settings = get_settings()
    max_size = settings.max_upload_size_bytes

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / file.filename
    total_bytes = 0
    try:
        with tmp_path.open("wb") as f:
            while chunk := await file.read(_COPY_CHUNK_SIZE):
                total_bytes += len(chunk)
                if total_bytes > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"File exceeds maximum upload size of {max_size} bytes",
                    )
                f.write(chunk)
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    job_id = jobs.try_create_job(
        current_user.id, str(tmp_path), file.filename, settings.max_active_jobs_per_user
    )
    if job_id is None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You already have {settings.max_active_jobs_per_user} uploads in progress. "
                "Wait for one to finish before starting another."
            ),
        )
    background_tasks.add_task(
        jobs.run_ingestion_job, job_id, str(tmp_path), file.filename, settings, current_user.id
    )

    return {"job_id": job_id}


@router.get("/jobs")
def list_active_jobs(current_user: CurrentUser = Depends(get_current_user)) -> JobListResponse:
    """Return the caller's PENDING/PROCESSING/FAILED jobs, account-wide (ERP-095).

    Unlike `GET /jobs/{job_id}`, this isn't scoped to a job ID the caller already knows -- it's
    what lets a second device (or a page reload that lost the local job ID) discover in-progress
    or failed uploads it didn't itself start.
    """
    return JobListResponse(
        jobs=[
            JobSummary(
                job_id=job_id, filename=record.filename, status=record.status, error=record.error
            )
            for job_id, record in jobs.list_active_jobs(current_user.id)
        ]
    )


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: str, current_user: CurrentUser = Depends(get_current_user)
) -> JobStatusResponse:
    """Return the current status (and result or error, once finished) of an ingestion job.

    Returns 404 (not just for an unknown ID, but also for a job owned by a different user)
    so a caller can't distinguish "doesn't exist" from "exists but isn't yours".
    """
    record = jobs.get_job(job_id)
    if record is None or record.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(status=record.status, result=record.result, error=record.error)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    """Re-run ingestion for a failed job's original uploaded file, without a fresh upload.

    404 if `job_id` is unknown, not owned by the caller, or not currently `failed` (matching
    the existing job-status-check 404 convention). Returns a brand new `job_id` -- the
    original failed job record is left untouched, this schedules a separate job.
    """
    settings = get_settings()
    retried = jobs.retry_job(job_id, current_user.id)
    if retried is None:
        raise HTTPException(status_code=404, detail="Failed job not found")

    new_job_id, pdf_path, filename = retried
    background_tasks.add_task(
        jobs.run_ingestion_job, new_job_id, pdf_path, filename, settings, current_user.id
    )
    return {"job_id": new_job_id}


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_endpoint(job_id: str, current_user: CurrentUser = Depends(get_current_user)) -> None:
    """Delete a failed job's record and temp file. 404 if unknown, not owned, or not failed."""
    if not jobs.delete_job(job_id, current_user.id):
        raise HTTPException(status_code=404, detail="Failed job not found")


@documents_router.get("")
def list_documents_endpoint(current_user: CurrentUser = Depends(get_current_user)) -> DocumentListResponse:
    """Return the caller's successfully ingested documents, newest first."""
    return list_documents(current_user.id)


@documents_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document_endpoint(document_id: str, current_user: CurrentUser = Depends(get_current_user)) -> None:
    """Delete a document, its chunks, and its FAISS vectors. 404 if unknown or not owned by the caller."""
    deleted = delete_document_and_vectors(document_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")


@documents_router.get("/{document_id}/chunks/{chunk_id}")
def get_chunk_endpoint(
    document_id: str, chunk_id: str, current_user: CurrentUser = Depends(get_current_user)
) -> ChunkDetailResponse:
    """Return one chunk's full text for the source panel. 404 if unknown or not owned by the caller."""
    chunk = get_chunk_detail(document_id, chunk_id, current_user.id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk
