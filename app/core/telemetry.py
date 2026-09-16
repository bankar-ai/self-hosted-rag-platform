"""OpenTelemetry setup: tracer/meter providers, auto-instrumentation, and metrics export.

Metrics export both ways at once: pull-based via the `/metrics` registry (local Prometheus) and
push-based via OTLP (Grafana Cloud on the live deployment, ERP-042).

Called once from `app.main` at startup. Never load-bearing: any failure during setup is logged
and swallowed rather than preventing the app from starting or serving requests (observability
must not be able to take the app down).
"""

import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter as GrpcOTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcOTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as HttpOTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpOTLPSpanExporter,
)
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Tracer

from app.core.db import get_engine

logger = logging.getLogger(__name__)


def get_tracer() -> Tracer:
    """Return this process's OTel tracer, used by hand-written spans across the codebase."""
    return trace.get_tracer(__name__)


def get_meter() -> Meter:
    """Return this process's OTel meter, used by hand-written metrics across the codebase."""
    return metrics.get_meter(__name__)


def _build_span_exporter() -> SpanExporter:
    """Pick the OTLP span exporter matching `OTEL_EXPORTER_OTLP_PROTOCOL`.

    OTel's own standard env var; defaults to `"grpc"` per the OTel spec, matching the local
    `docker-compose` Jaeger service on port 4317. Grafana Cloud's OTLP gateway instead requires
    `"http/protobuf"`. Both exporters read `OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_EXPORTER_OTLP_HEADERS`
    from the environment automatically when constructed with no args.
    """
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    if protocol == "http/protobuf":
        return HttpOTLPSpanExporter()
    return GrpcOTLPSpanExporter()


def _build_otlp_metric_exporter() -> MetricExporter:
    """Pick the OTLP metric exporter matching `OTEL_EXPORTER_OTLP_PROTOCOL`.

    Mirrors `_build_span_exporter`/`app.core.logging_config._build_log_exporter`'s protocol
    selection: `"grpc"` (default, unset) for local Jaeger/dev, `"http/protobuf"` for Grafana
    Cloud's OTLP gateway.
    """
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    if protocol == "http/protobuf":
        return HttpOTLPMetricExporter()
    return GrpcOTLPMetricExporter()


def _build_metric_readers() -> list[MetricReader]:
    """Build the metric readers for the global `MeterProvider`.

    Two readers, both always active: `PrometheusMetricReader` (pull-based, backs the local
    `/metrics` endpoint `docker-compose`'s Prometheus service scrapes) and a push-based
    `PeriodicExportingMetricReader` over OTLP (ERP-042), so the same hand-written +
    auto-instrumented metrics also reach Grafana Cloud on the live deployment, which has no local
    Prometheus to scrape it (same RAM-budget constraint that shaped ERP-038/ERP-039's push-based
    trace/log export).
    """
    return [
        PrometheusMetricReader(),
        PeriodicExportingMetricReader(_build_otlp_metric_exporter()),
    ]


def configure_telemetry(app: FastAPI) -> None:
    """Configure global OTel tracer/meter providers and auto-instrument FastAPI/SQLAlchemy/Redis/httpx.

    Any exception during setup is logged and swallowed -- the app still starts and serves requests
    normally, just without instrumentation, rather than failing to start because a trace/metrics
    backend isn't reachable yet.
    """
    try:
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(_build_span_exporter()))
        trace.set_tracer_provider(tracer_provider)

        meter_provider = MeterProvider(metric_readers=_build_metric_readers())
        metrics.set_meter_provider(meter_provider)

        FastAPIInstrumentor.instrument_app(app)
        SQLAlchemyInstrumentor().instrument(engine=get_engine())
        RedisInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.exception("Failed to configure telemetry; continuing without instrumentation")
