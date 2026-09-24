# tests/ingestion/test_jobs.py
import os
import uuid

from app.core.db import get_session_factory
from app.embedding.index import OwnerFaissIndexStore
from app.ingestion.config import IngestionSettings
from app.ingestion.jobs import (
    create_job,
    delete_job,
    get_job,
    list_active_jobs,
    retry_job,
    run_ingestion_job,
    try_create_job,
)
from app.ingestion.models import ChunkRecord
from app.ingestion.schemas import JobStatus

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


class _FakeEmbeddingClient:
    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


def test_create_job_starts_pending():
    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    record = get_job(job_id)
    assert record.status == JobStatus.PENDING
    assert record.result is None
    assert record.error is None


def test_get_job_returns_none_for_unknown_id():
    assert get_job("does-not-exist") is None


def test_run_ingestion_job_marks_done_on_success(simple_text_pdf, tmp_path):
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        _TEST_OWNER_ID,
        embedding_client=_FakeEmbeddingClient(),
        faiss_index_store=OwnerFaissIndexStore(str(tmp_path), dimension=4),
    )

    record = get_job(job_id)
    assert record.status == JobStatus.DONE
    assert record.result is not None
    assert record.error is None


def test_run_ingestion_job_marks_failed_on_bad_path():
    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings(), _TEST_OWNER_ID)

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.result is None
    assert record.error is not None


def test_run_ingestion_job_logs_on_failure(caplog):
    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    with caplog.at_level("ERROR"):
        run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings(), _TEST_OWNER_ID)

    assert any(job_id in record.message for record in caplog.records)
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_run_ingestion_job_persists_chunks_and_vectors(simple_text_pdf, tmp_path):
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)

    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        _TEST_OWNER_ID,
        embedding_client=_FakeEmbeddingClient(),
        faiss_index_store=faiss_index_store,
    )

    record = get_job(job_id)
    assert record.status == JobStatus.DONE
    document_id = record.result.document_id

    hits = faiss_index_store.search(_TEST_OWNER_ID, [0.1] * 4, k=1000)
    assert len(hits) == len(record.result.chunks)

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).all()
        assert len(rows) == len(record.result.chunks)


def test_run_ingestion_job_marks_failed_if_persistence_fails(simple_text_pdf, tmp_path):
    class _BrokenEmbeddingClient:
        def embed(self, texts):
            raise RuntimeError("embedding backend unreachable")

    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        _TEST_OWNER_ID,
        embedding_client=_BrokenEmbeddingClient(),
        faiss_index_store=OwnerFaissIndexStore(str(tmp_path), dimension=4),
    )

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.error is not None
    assert "embedding backend unreachable" in record.error


def test_run_ingestion_job_deletes_temp_file_on_success(simple_text_pdf, tmp_path):
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    job_id = create_job(_TEST_OWNER_ID, simple_text_pdf, "simple.pdf")
    run_ingestion_job(
        job_id,
        simple_text_pdf,
        "simple.pdf",
        _settings(),
        _TEST_OWNER_ID,
        embedding_client=_FakeEmbeddingClient(),
        faiss_index_store=OwnerFaissIndexStore(str(tmp_path / "faiss"), dimension=4),
    )

    assert not os.path.exists(simple_text_pdf)


def test_run_ingestion_job_keeps_temp_file_on_failure():
    job_id = create_job(_TEST_OWNER_ID, "/no/such/file.pdf", "missing.pdf")
    run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings(), _TEST_OWNER_ID)

    record = get_job(job_id)
    assert record.status == JobStatus.FAILED
    assert record.pdf_path == "/no/such/file.pdf"


def test_retry_job_creates_a_new_job_for_a_failed_one():
    job_id = create_job(_TEST_OWNER_ID, "/no/such/file.pdf", "missing.pdf")
    run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings(), _TEST_OWNER_ID)

    retried = retry_job(job_id, _TEST_OWNER_ID)

    assert retried is not None
    new_job_id, pdf_path, filename = retried
    assert new_job_id != job_id
    assert pdf_path == "/no/such/file.pdf"
    assert filename == "missing.pdf"
    new_record = get_job(new_job_id)
    assert new_record.status == JobStatus.PENDING
    # The original failed record is untouched, not mutated in place.
    assert get_job(job_id).status == JobStatus.FAILED


def test_retry_job_returns_none_for_unknown_job():
    assert retry_job("does-not-exist", _TEST_OWNER_ID) is None


def test_retry_job_returns_none_for_a_job_that_is_not_failed():
    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")

    assert retry_job(job_id, _TEST_OWNER_ID) is None


def test_retry_job_returns_none_for_wrong_owner():
    job_id = create_job(_TEST_OWNER_ID, "/no/such/file.pdf", "missing.pdf")
    run_ingestion_job(job_id, "/no/such/file.pdf", "missing.pdf", _settings(), _TEST_OWNER_ID)

    assert retry_job(job_id, uuid.uuid4()) is None


def test_delete_job_removes_the_job_and_unlinks_its_temp_file(tmp_path, monkeypatch):
    import app.ingestion.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "ingest_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    pdf_path = tmp_path / "leftover.pdf"
    pdf_path.write_bytes(b"placeholder -- never actually parsed, ingest_pdf is monkeypatched to fail")
    job_id = create_job(_TEST_OWNER_ID, str(pdf_path), "leftover.pdf")
    run_ingestion_job(job_id, str(pdf_path), "leftover.pdf", _settings(), _TEST_OWNER_ID)
    assert get_job(job_id).status == JobStatus.FAILED
    assert pdf_path.exists()

    result = delete_job(job_id, _TEST_OWNER_ID)

    assert result is True
    assert get_job(job_id) is None
    assert not pdf_path.exists()


def test_delete_job_returns_false_for_unknown_job():
    assert delete_job("does-not-exist", _TEST_OWNER_ID) is False


def test_delete_job_returns_false_for_a_job_that_is_not_failed():
    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    assert delete_job(job_id, _TEST_OWNER_ID) is False


def test_try_create_job_returns_none_when_owner_at_cap():
    owner_id = uuid.uuid4()
    job_ids = [
        try_create_job(owner_id, f"/tmp/{i}.pdf", f"{i}.pdf", max_active=3) for i in range(3)
    ]
    assert all(job_id is not None for job_id in job_ids)

    rejected = try_create_job(owner_id, "/tmp/4.pdf", "4.pdf", max_active=3)
    assert rejected is None


def test_try_create_job_counts_only_pending_and_processing_for_that_owner():
    import app.ingestion.jobs as jobs_module

    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    job_id = try_create_job(owner_id, "/tmp/1.pdf", "1.pdf", max_active=1)
    assert job_id is not None

    # A different owner's job doesn't count against this owner's cap.
    other_job_id = try_create_job(other_owner_id, "/tmp/2.pdf", "2.pdf", max_active=1)
    assert other_job_id is not None

    # A DONE job for the same owner frees up capacity.
    with jobs_module._lock:
        jobs_module._jobs[job_id].status = JobStatus.DONE
    freed = try_create_job(owner_id, "/tmp/3.pdf", "3.pdf", max_active=1)
    assert freed is not None


def test_list_active_jobs_excludes_done_but_includes_pending_processing_failed():
    owner_id = uuid.uuid4()
    pending_id = create_job(owner_id, "/tmp/pending.pdf", "pending.pdf")
    failed_id = create_job(owner_id, "/no/such/file.pdf", "failed.pdf")
    run_ingestion_job(failed_id, "/no/such/file.pdf", "failed.pdf", _settings(), owner_id)
    done_id = create_job(owner_id, "/tmp/done.pdf", "done.pdf")
    import app.ingestion.jobs as jobs_module

    with jobs_module._lock:
        jobs_module._jobs[done_id].status = JobStatus.DONE

    active = dict(list_active_jobs(owner_id))

    assert set(active) == {pending_id, failed_id}
    assert active[pending_id].status == JobStatus.PENDING
    assert active[failed_id].status == JobStatus.FAILED


def test_list_active_jobs_scoped_to_owner():
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    create_job(owner_id, "/tmp/mine.pdf", "mine.pdf")
    create_job(other_owner_id, "/tmp/theirs.pdf", "theirs.pdf")

    active = list_active_jobs(owner_id)

    assert len(active) == 1
    assert active[0][1].filename == "mine.pdf"


def test_delete_job_returns_false_for_wrong_owner(tmp_path, monkeypatch):
    import app.ingestion.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "ingest_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    pdf_path = tmp_path / "leftover2.pdf"
    pdf_path.write_bytes(b"placeholder -- never actually parsed, ingest_pdf is monkeypatched to fail")
    job_id = create_job(_TEST_OWNER_ID, str(pdf_path), "leftover2.pdf")
    run_ingestion_job(job_id, str(pdf_path), "leftover2.pdf", _settings(), _TEST_OWNER_ID)

    assert delete_job(job_id, uuid.uuid4()) is False
    assert get_job(job_id).status == JobStatus.FAILED
