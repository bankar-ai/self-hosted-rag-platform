"""In-memory async ingestion job tracking (no persistent queue; single-process only)."""

import logging
import threading
import uuid
from pathlib import Path

from app.core.telemetry import get_meter
from app.embedding.client import EmbeddingClient
from app.embedding.index import OwnerFaissIndexStore
from app.embedding.service import embed_and_persist
from app.ingestion.config import IngestionSettings
from app.ingestion.schemas import IngestResponse, JobStatus
from app.ingestion.service import ingest_pdf

logger = logging.getLogger(__name__)

_jobs_counter = get_meter().create_counter(
    "ingestion_jobs_total", description="Completed ingestion jobs by outcome"
)

_jobs: dict[str, "JobRecord"] = {}
_lock = threading.Lock()


class JobRecord:
    """Mutable state for one tracked ingestion job."""

    def __init__(self, owner_id: uuid.UUID, pdf_path: str, filename: str) -> None:
        """Initialize a new job in PENDING status, owned by `owner_id`, with no result or error yet.

        `pdf_path`/`filename` are retained (not just passed through to `run_ingestion_job`) so
        a later `retry_job` call can re-run ingestion against the same uploaded bytes without
        requiring the caller to re-upload the file (ERP-053).
        """
        self.owner_id = owner_id
        self.pdf_path = pdf_path
        self.filename = filename
        self.status: JobStatus = JobStatus.PENDING
        self.result: IngestResponse | None = None
        self.error: str | None = None


def create_job(owner_id: uuid.UUID, pdf_path: str, filename: str) -> str:
    """Register a new PENDING job owned by `owner_id` and return its ID."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobRecord(owner_id, pdf_path, filename)
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    """Look up a job by ID, or None if it doesn't exist."""
    with _lock:
        return _jobs.get(job_id)


def retry_job(job_id: str, owner_id: uuid.UUID) -> tuple[str, str, str] | None:
    """Create a fresh job re-running ingestion for `job_id`'s original uploaded file.

    Returns `(new_job_id, pdf_path, filename)` -- the latter two so the caller can schedule
    `run_ingestion_job` without a second lookup -- or `None` if `job_id` doesn't exist, isn't
    owned by `owner_id`, or isn't currently `FAILED` (retrying a still-running or
    already-succeeded job makes no sense). The original job record is left untouched -- this
    creates a new job, it does not mutate the old one in place.
    """
    with _lock:
        record = _jobs.get(job_id)
        if record is None or record.owner_id != owner_id or record.status != JobStatus.FAILED:
            return None
        pdf_path, filename = record.pdf_path, record.filename
    new_job_id = create_job(owner_id, pdf_path, filename)
    return new_job_id, pdf_path, filename


def run_ingestion_job(
    job_id: str,
    pdf_path: str,
    filename: str,
    settings: IngestionSettings,
    owner_id: uuid.UUID,
    embedding_client: EmbeddingClient | None = None,
    faiss_index_store: OwnerFaissIndexStore | None = None,
) -> None:
    """Run ingestion for `job_id`, recording DONE + result or FAILED + error on the job record.

    On success, also embeds and durably persists the resulting chunks (Postgres + FAISS),
    stamping `owner_id` as the resulting document's owner — a DONE job means the data is
    embedded and persisted, not just held in memory. The uploaded temp file at `pdf_path` is
    deleted once no longer needed on success; on failure it is deliberately kept so
    `retry_job` can re-run ingestion against the same bytes without a fresh upload (ERP-053).
    """
    with _lock:
        _jobs[job_id].status = JobStatus.PROCESSING

    try:
        result = ingest_pdf(pdf_path, filename, settings)
        embed_and_persist(
            document_id=result.document_id,
            source_filename=filename,
            chunks=result.chunks,
            owner_id=owner_id,
            embedding_client=embedding_client,
            faiss_index_store=faiss_index_store,
        )
    except Exception as exc:  # noqa: BLE001 - job failure is reported via status, not raised
        logger.exception("Ingestion job %s failed for file %r", job_id, filename)
        error_msg = str(exc)
        with _lock:
            _jobs[job_id].status = JobStatus.FAILED
            _jobs[job_id].error = error_msg
        _jobs_counter.add(1, {"status": "failed"})
        # Force garbage collection to release file handles held by C extensions (e.g. pymupdf).
        # On Windows, file handles from C extensions may not be released immediately.
        import gc
        gc.collect()
        return

    with _lock:
        _jobs[job_id].status = JobStatus.DONE
        _jobs[job_id].result = result
    _jobs_counter.add(1, {"status": "done"})

    # Only the uploaded file itself, not its whole parent directory: in production that
    # directory is a dedicated `tempfile.mkdtemp()` per upload (safe to remove entirely once
    # empty), but tests may reuse a shared directory for other purposes (e.g. a FAISS index
    # path) -- `rmdir` is a no-op (via the caught exception) if anything else still lives there.
    Path(pdf_path).unlink(missing_ok=True)
    try:
        Path(pdf_path).parent.rmdir()
    except OSError:
        pass


def delete_job(job_id: str, owner_id: uuid.UUID) -> bool:
    """Delete a FAILED job's record and its retained temp file (ERP-072).

    A failed job's uploaded file is deliberately kept on disk so `retry_job` can reuse it
    without a re-upload -- but if the caller instead dismisses the job for good, nothing
    previously cleaned up either the in-memory record or that file, leaking disk space on a
    resource-constrained deployment. Returns `False` (mapped to `404` by the router) if the
    job doesn't exist, isn't owned by `owner_id`, or isn't currently `FAILED` -- mirroring
    `retry_job`'s exact guard, since dismissing only makes sense for the same states retrying
    would apply to.
    """
    import gc
    import time

    with _lock:
        record = _jobs.get(job_id)
        if record is None or record.owner_id != owner_id or record.status != JobStatus.FAILED:
            return False
        pdf_path = record.pdf_path
        del _jobs[job_id]

    # Force garbage collection multiple times and wait to release lingering file handles
    # (especially on Windows where PDF parsing libraries may not release them immediately)
    gc.collect()
    gc.collect()
    time.sleep(0.2)

    # Try to delete the file with retries
    path_obj = Path(pdf_path)
    for attempt in range(15):
        try:
            path_obj.unlink(missing_ok=True)
            break
        except OSError:
            if attempt < 14:
                # More aggressive GC between attempts
                gc.collect()
                time.sleep(0.1 * (attempt + 1))
            # Final attempt silently fails, file will remain but job record is deleted

    try:
        Path(pdf_path).parent.rmdir()
    except OSError:
        pass
    return True
