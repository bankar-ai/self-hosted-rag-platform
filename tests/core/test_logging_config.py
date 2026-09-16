import logging
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpOTLPLogExporter,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging_config import TraceIdFilter, _build_log_exporter, configure_logging


def test_trace_id_filter_injects_hex_trace_id_when_span_active():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
    filt = TraceIdFilter()

    with tracer.start_as_current_span("span"):
        filt.filter(record)
        span = trace.get_current_span()
        expected = format(span.get_span_context().trace_id, "032x")

    assert record.trace_id == expected


def test_trace_id_filter_uses_placeholder_with_no_active_span():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
    filt = TraceIdFilter()

    filt.filter(record)

    assert record.trace_id == "-"


def test_configure_logging_installs_filter_on_root_logger():
    configure_logging()

    root = logging.getLogger()
    assert any(
        isinstance(f, TraceIdFilter) for handler in root.handlers for f in handler.filters
    )


def test_configure_logging_does_not_raise_when_otlp_endpoint_unreachable(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:1")

    configure_logging()  # must not raise even though nothing is listening on :1


def test_configure_logging_degrades_gracefully_on_setup_error():
    with patch(
        "app.core.logging_config.LoggerProvider", side_effect=RuntimeError("boom")
    ):
        configure_logging()  # must not raise

    root = logging.getLogger()
    assert any(
        isinstance(f, TraceIdFilter) for handler in root.handlers for f in handler.filters
    )


def test_build_log_exporter_uses_http_when_protocol_is_http_protobuf(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    assert isinstance(_build_log_exporter(), HttpOTLPLogExporter)
