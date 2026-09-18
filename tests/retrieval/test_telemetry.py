import uuid

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.retrieval import service as service_module


class _StubEmbeddingClient:
    def embed(self, texts):
        return [[0.1, 0.2]]


class _StubFaissIndexStore:
    def search(self, owner_id, vector, k, allowed_vector_ids=None):
        return []


class _NoOpCache:
    def get(self, cache_key):
        return None

    def set(self, cache_key, results):
        pass


class _StubSettings:
    dimension = 2
    faiss_index_dir = ":unused:"


def test_search_produces_fuse_span_even_with_no_hits(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(service_module, "get_tracer", lambda: provider.get_tracer("test"))

    service_module.search(
        query="hello",
        top_k=5,
        owner_id=uuid.uuid4(),
        settings=_StubSettings(),
        embedding_client=_StubEmbeddingClient(),
        faiss_index_store=_StubFaissIndexStore(),
        cache=_NoOpCache(),
    )

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "retrieval.fuse" in span_names
