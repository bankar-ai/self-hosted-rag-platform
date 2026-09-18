import uuid
from unittest.mock import patch

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import app.ingestion.jobs as jobs_module
from app.ingestion.config import IngestionSettings
from app.ingestion.jobs import create_job, get_job, run_ingestion_job


def test_run_ingestion_job_records_failed_status_metric(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(
        jobs_module, "_jobs_counter", provider.get_meter("test").create_counter("ingestion_jobs_total")
    )

    job_id = create_job(uuid.uuid4(), "/tmp/unused.pdf", "unused.pdf")
    with patch("app.ingestion.jobs.ingest_pdf", side_effect=ValueError("bad pdf")):
        run_ingestion_job(
            job_id, "does-not-matter.pdf", "does-not-matter.pdf", IngestionSettings(), uuid.uuid4()
        )

    job = get_job(job_id)
    assert job is not None
    assert job.status.value == "failed"

    data = reader.get_metrics_data()
    points = [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        for point in m.data.data_points
    ]
    assert any(point.attributes.get("status") == "failed" for point in points)
