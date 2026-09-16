"""Trace-ID-correlated structured logging setup.

Injects the active OpenTelemetry span's trace ID into every log record, so a log line can be
pivoted straight to its trace in Jaeger. Never raises: a record with no active span (e.g. at
startup, or in a background thread with no span context) gets the placeholder `"-"`.

Also exports log records via OTLP (ERP-039), in-process -- no separate log-shipping agent/daemon,
which the live deployment's tight VM memory budget can't spare (see ERP-037/ERP-038). Reuses the
same `OTEL_EXPORTER_OTLP_*` env vars as `app.core.telemetry`'s span exporter.
"""

import logging
import os

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter as GrpcOTLPLogExporter,
)
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpOTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter

logger = logging.getLogger(__name__)

_NO_TRACE_PLACEHOLDER = "-"


class TraceIdFilter(logging.Filter):
    """A `logging.Filter` that sets `record.trace_id` from the current OTel span, if any."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set `record.trace_id` and always allow the record through (never filters anything out)."""
        span = trace.get_current_span()
        context = span.get_span_context()
        if context.is_valid:
            record.trace_id = format(context.trace_id, "032x")
        else:
            record.trace_id = _NO_TRACE_PLACEHOLDER
        return True


def _build_log_exporter() -> LogRecordExporter:
    """Pick the OTLP log exporter matching `OTEL_EXPORTER_OTLP_PROTOCOL`.

    Mirrors `app.core.telemetry._build_span_exporter`'s protocol selection: `"grpc"` (default,
    unset) for local Jaeger/dev, `"http/protobuf"` for Grafana Cloud's OTLP gateway.
    """
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    if protocol == "http/protobuf":
        return HttpOTLPLogExporter()
    return GrpcOTLPLogExporter()


def configure_logging() -> None:
    """Install `TraceIdFilter` on the root logger and format log records to include the trace ID.

    Also attaches an OTLP `LoggingHandler` so log records export to the same backend as traces
    (ERP-039). Any exception during OTLP setup is logged and swallowed -- stdout logging (this
    process's only guaranteed sink) must never depend on an external backend being reachable.
    """
    handler = logging.StreamHandler()
    handler.addFilter(TraceIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [trace_id=%(trace_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    try:
        logger_provider = LoggerProvider()
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(_build_log_exporter()))
        set_logger_provider(logger_provider)
        root.addHandler(LoggingHandler(logger_provider=logger_provider))
    except Exception:
        logger.exception("Failed to configure OTLP log export; continuing with stdout logging only")
