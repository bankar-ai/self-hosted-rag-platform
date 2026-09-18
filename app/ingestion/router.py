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
from app.ingestion.schemas import DocumentListResponse, JobStatusResponse
from app.ingestion.service import list_documents

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

    job_id = jobs.create_job(current_user.id)
    background_tasks.add_task(
        jobs.run_ingestion_job, job_id, str(tmp_path), file.filename, settings, current_user.id
    )

    return {"job_id": job_id}


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
