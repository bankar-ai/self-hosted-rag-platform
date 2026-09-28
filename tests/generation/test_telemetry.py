from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.generation.client as client_module
from app.generation.client import OllamaLLMClient


class _StubMessage:
    content = "an answer"


class _StubResponse:
    message = _StubMessage()
    prompt_eval_count = None
    eval_count = None


class _StubOllama:
    def chat(self, **kwargs):
        return _StubResponse()


def test_generate_produces_a_span_and_records_duration(monkeypatch):
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    monkeypatch.setattr(client_module, "get_tracer", lambda: tracer_provider.get_tracer("test"))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    monkeypatch.setattr(
        client_module,
        "_duration_histogram",
        meter_provider.get_meter("test").create_histogram("llm_generation_duration_seconds"),
    )

    client = OllamaLLMClient.__new__(OllamaLLMClient)
    client._client = _StubOllama()
    client._model = "test-model"
    client._temperature = 0.0

    result = client.generate("system", "user")

    assert result == "an answer"
    span_names = [span.name for span in span_exporter.get_finished_spans()]
    assert "llm.generate" in span_names

    data = metric_reader.get_metrics_data()
    metric_names = {
        m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
    }
    assert "llm_generation_duration_seconds" in metric_names
