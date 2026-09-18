import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.auth_helpers import register_and_login

client = TestClient(app)


@pytest.fixture
def auth_headers():
    return register_and_login(client, "generation")


@pytest.fixture(autouse=True)
def _stub_generation_backend(monkeypatch):
    """Stub retrieval and the LLM client so no real Ollama/Postgres call is made."""

    def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False):
        return []

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    yield


def test_query_returns_no_context_answer_when_retrieval_empty(auth_headers):
    response = client.post("/generation/query", json={"query": "anything"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert "don't have enough information" in body["answer"]


def test_query_rejects_empty_query_string(auth_headers):
    response = client.post("/generation/query", json={"query": ""}, headers=auth_headers)
    assert response.status_code == 422


def test_query_rejects_top_k_out_of_bounds(auth_headers):
    response = client.post("/generation/query", json={"query": "x", "top_k": 0}, headers=auth_headers)
    assert response.status_code == 422


def test_query_returns_answer_with_citations(monkeypatch, auth_headers):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer [1]"
    )

    response = client.post("/generation/query", json={"query": "what is X?"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "the answer [1]"
    assert body["citations"] == [
        {
            "chunk_id": "c1",
            "document_id": "doc-1",
            "section_path": ["Intro"],
            "page_start": 1,
            "page_end": 1,
            "source_filename": "doc.pdf",
            "score": 0.9,
            "reranked": False,
        }
    ]


def test_query_returns_503_when_llm_backend_unavailable(monkeypatch, auth_headers):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )

    from app.generation.client import OllamaLLMClient

    def _raise_generate(self, system_prompt, user_prompt):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(OllamaLLMClient, "generate", _raise_generate)

    response = client.post("/generation/query", json={"query": "what is X?"}, headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Generation query failed"}


def test_query_omitted_conversation_id_returns_null_conversation_id(auth_headers):
    response = client.post("/generation/query", json={"query": "anything"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["conversation_id"] is None


def test_query_with_conversation_id_continues_across_two_calls(monkeypatch, auth_headers):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    conversation_id = str(uuid.uuid4())
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="deployment has three steps",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    retrieval_queries = []

    def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False):
        retrieval_queries.append(query)
        return [chunk]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)

    from app.generation.client import OllamaLLMClient

    # Three canned answers are needed, not two: the first HTTP call consumes one
    # (final synthesis, turn 1 has no history to rewrite); the second HTTP call
    # consumes two (query rewrite, since history now exists, then final synthesis).
    answers = iter(["it has three steps.", "the second step is test.", "here is more detail."])
    monkeypatch.setattr(
        OllamaLLMClient,
        "generate",
        lambda self, system_prompt, user_prompt: next(answers),
    )

    first = client.post(
        "/generation/query",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert first.status_code == 200
    assert first.json()["conversation_id"] == conversation_id

    second = client.post(
        "/generation/query",
        json={"query": "what about the second one?", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    assert retrieval_queries[0] == "what is the deployment process?"
    assert retrieval_queries[1] != "what about the second one?"


def test_get_conversation_returns_404_for_unknown_id(auth_headers):
    import uuid

    response = client.get(f"/conversations/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


def test_get_conversation_returns_history_ordered_oldest_first(monkeypatch, auth_headers):
    import uuid

    from app.generation.client import OllamaLLMClient
    from app.retrieval.schemas import RetrievedChunk

    conversation_id = str(uuid.uuid4())
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="deployment has three steps",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [chunk]
    )
    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    create_response = client.post(
        "/generation/query",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert create_response.status_code == 200

    response = client.get(f"/conversations/{conversation_id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == "what is the deployment process?"
    assert body["messages"][1]["content"] == "the answer"


def test_query_returns_404_when_conversation_id_belongs_to_another_user(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    conversation_id = str(uuid.uuid4())
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    headers_a = register_and_login(client, "generation-owner-a")
    headers_b = register_and_login(client, "generation-owner-b")

    create_response = client.post(
        "/generation/query",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
        headers=headers_a,
    )
    assert create_response.status_code == 200

    response = client.post(
        "/generation/query",
        json={"query": "what about the second one?", "conversation_id": conversation_id},
        headers=headers_b,
    )

    assert response.status_code == 404


def test_get_conversation_returns_404_when_conversation_belongs_to_another_user(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    conversation_id = str(uuid.uuid4())
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    headers_a = register_and_login(client, "generation-history-owner-a")
    headers_b = register_and_login(client, "generation-history-owner-b")

    create_response = client.post(
        "/generation/query",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
        headers=headers_a,
    )
    assert create_response.status_code == 200

    response = client.get(f"/conversations/{conversation_id}", headers=headers_b)

    assert response.status_code == 404


def test_list_conversations_empty_for_new_user(auth_headers):
    response = client.get("/conversations", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"conversations": []}


def test_list_conversations_returns_newest_first_with_preview(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    headers = register_and_login(client, "generation-list-conversations")
    conv_a, conv_b = str(uuid.uuid4()), str(uuid.uuid4())

    client.post(
        "/generation/query", json={"query": "first question", "conversation_id": conv_a}, headers=headers
    )
    client.post(
        "/generation/query", json={"query": "second question", "conversation_id": conv_b}, headers=headers
    )

    response = client.get("/conversations", headers=headers)

    assert response.status_code == 200
    conversations = response.json()["conversations"]
    assert [c["conversation_id"] for c in conversations] == [conv_b, conv_a]
    assert conversations[0]["preview"] == "second question"
    assert conversations[1]["preview"] == "first question"


def test_list_conversations_does_not_include_another_users_conversations(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    headers_a = register_and_login(client, "generation-list-conversations-owner-a")
    headers_b = register_and_login(client, "generation-list-conversations-owner-b")

    client.post(
        "/generation/query",
        json={"query": "a's question", "conversation_id": str(uuid.uuid4())},
        headers=headers_a,
    )

    response = client.get("/conversations", headers=headers_b)

    assert response.status_code == 200
    assert response.json() == {"conversations": []}


def test_rename_conversation_returns_204_and_persists(monkeypatch, auth_headers):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    conversation_id = str(uuid.uuid4())
    client.post(
        "/generation/query",
        json={"query": "first question", "conversation_id": conversation_id},
        headers=auth_headers,
    )

    response = client.patch(
        f"/conversations/{conversation_id}", json={"title": "My renamed chat"}, headers=auth_headers
    )
    assert response.status_code == 204

    listed = client.get("/conversations", headers=auth_headers)
    renamed = next(c for c in listed.json()["conversations"] if c["conversation_id"] == conversation_id)
    assert renamed["title"] == "My renamed chat"


def test_rename_conversation_404_for_unknown_id(auth_headers):
    import uuid

    response = client.patch(
        f"/conversations/{uuid.uuid4()}", json={"title": "x"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_rename_conversation_404_for_another_users_conversation(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    headers_a = register_and_login(client, "generation-rename-owner-a")
    headers_b = register_and_login(client, "generation-rename-owner-b")
    conversation_id = str(uuid.uuid4())
    client.post(
        "/generation/query",
        json={"query": "a's question", "conversation_id": conversation_id},
        headers=headers_a,
    )

    response = client.patch(
        f"/conversations/{conversation_id}", json={"title": "hijacked"}, headers=headers_b
    )
    assert response.status_code == 404


def test_rename_conversation_rejects_empty_title(auth_headers):
    import uuid

    response = client.patch(
        f"/conversations/{uuid.uuid4()}", json={"title": ""}, headers=auth_headers
    )
    assert response.status_code == 422


def test_rename_conversation_409_on_duplicate_title_for_same_owner(monkeypatch, auth_headers):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    conv_a, conv_b = str(uuid.uuid4()), str(uuid.uuid4())
    client.post(
        "/generation/query", json={"query": "a", "conversation_id": conv_a}, headers=auth_headers
    )
    client.post(
        "/generation/query", json={"query": "b", "conversation_id": conv_b}, headers=auth_headers
    )
    rename_a = client.patch(
        f"/conversations/{conv_a}", json={"title": "Shared Name"}, headers=auth_headers
    )
    assert rename_a.status_code == 204

    response = client.patch(
        f"/conversations/{conv_b}", json={"title": "shared name"}, headers=auth_headers
    )
    assert response.status_code == 409


def test_rename_conversation_allows_renaming_to_its_own_current_title(monkeypatch, auth_headers):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    conversation_id = str(uuid.uuid4())
    client.post(
        "/generation/query",
        json={"query": "a question", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    first = client.patch(
        f"/conversations/{conversation_id}", json={"title": "Same Name"}, headers=auth_headers
    )
    assert first.status_code == 204

    second = client.patch(
        f"/conversations/{conversation_id}", json={"title": "Same Name"}, headers=auth_headers
    )
    assert second.status_code == 204


def test_rename_conversation_does_not_conflict_across_different_owners(monkeypatch):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )

    headers_a = register_and_login(client, "generation-rename-dup-owner-a")
    headers_b = register_and_login(client, "generation-rename-dup-owner-b")
    conv_a, conv_b = str(uuid.uuid4()), str(uuid.uuid4())
    client.post(
        "/generation/query", json={"query": "a", "conversation_id": conv_a}, headers=headers_a
    )
    client.post(
        "/generation/query", json={"query": "b", "conversation_id": conv_b}, headers=headers_b
    )
    client.patch(f"/conversations/{conv_a}", json={"title": "Same Name"}, headers=headers_a)

    response = client.patch(
        f"/conversations/{conv_b}", json={"title": "Same Name"}, headers=headers_b
    )
    assert response.status_code == 204


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip("\n").split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((event, data))
    return events


def test_query_stream_returns_no_context_sse_when_retrieval_empty(auth_headers):
    from app.generation.service import NO_CONTEXT_ANSWER

    response = client.post(
        "/generation/query/stream", json={"query": "anything"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = _parse_sse(response.text)
    assert events == [
        ("token", {"text": NO_CONTEXT_ANSWER}),
        ("citations", {"citations": []}),
        ("done", {"conversation_id": None}),
    ]


def test_query_stream_returns_citations_tokens_and_done(monkeypatch, auth_headers):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    def _fake_generate_stream(self, system_prompt, user_prompt):
        yield "the "
        yield "answer [1]"

    monkeypatch.setattr(OllamaLLMClient, "generate_stream", _fake_generate_stream)

    response = client.post(
        "/generation/query/stream", json={"query": "what is X?"}, headers=auth_headers
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0] == ("token", {"text": "the "})
    assert events[1] == ("token", {"text": "answer [1]"})
    assert events[2] == (
        "citations",
        {
            "citations": [
                {
                    "chunk_id": "c1",
                    "document_id": "doc-1",
                    "section_path": ["Intro"],
                    "page_start": 1,
                    "page_end": 1,
                    "source_filename": "doc.pdf",
                    "score": 0.9,
                    "reranked": False,
                }
            ]
        },
    )
    assert events[3] == ("done", {"conversation_id": None})


def test_query_stream_yields_error_event_on_llm_failure(monkeypatch, auth_headers):
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])

    from app.generation.client import OllamaLLMClient

    def _raise_generate_stream(self, system_prompt, user_prompt):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover -- unreachable, only makes this a generator function

    monkeypatch.setattr(OllamaLLMClient, "generate_stream", _raise_generate_stream)

    response = client.post(
        "/generation/query/stream", json={"query": "what is X?"}, headers=auth_headers
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1] == ("error", {"detail": "Generation query failed"})


def test_query_stream_with_conversation_id_continues_across_two_calls(monkeypatch, auth_headers):
    import uuid

    from app.retrieval.schemas import RetrievedChunk

    conversation_id = str(uuid.uuid4())
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="deployment has three steps",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    retrieval_queries = []

    def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False):
        retrieval_queries.append(query)
        return [chunk]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)

    from app.generation.client import OllamaLLMClient

    stream_answers = iter([["it has ", "three steps."], ["the second step is test."]])
    generate_answers = iter(["the second question rewritten"])

    def _fake_generate_stream(self, system_prompt, user_prompt):
        yield from next(stream_answers)

    def _fake_generate(self, system_prompt, user_prompt):
        return next(generate_answers)

    monkeypatch.setattr(OllamaLLMClient, "generate_stream", _fake_generate_stream)
    monkeypatch.setattr(OllamaLLMClient, "generate", _fake_generate)

    first = client.post(
        "/generation/query/stream",
        json={"query": "what is the deployment process?", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert first.status_code == 200
    assert _parse_sse(first.text)[-1][0] == "done"
    assert _parse_sse(first.text)[-1][1]["conversation_id"] == conversation_id

    second = client.post(
        "/generation/query/stream",
        json={"query": "what about the second one?", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert _parse_sse(second.text)[-1][0] == "done"
    assert _parse_sse(second.text)[-1][1]["conversation_id"] == conversation_id

    assert retrieval_queries[0] == "what is the deployment process?"
    assert retrieval_queries[1] == "the second question rewritten"


def _post_query_with_conversation(monkeypatch, auth_headers, conversation_id):
    from app.generation.client import OllamaLLMClient
    from app.retrieval.schemas import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="doc-1",
        text="some text",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [chunk])
    monkeypatch.setattr(
        OllamaLLMClient, "generate", lambda self, system_prompt, user_prompt: "the answer"
    )
    return client.post(
        "/generation/query",
        json={"query": "a question", "conversation_id": conversation_id},
        headers=auth_headers,
    )


def test_query_returns_assistant_message_id_when_stateful(monkeypatch, auth_headers):
    import uuid

    response = _post_query_with_conversation(monkeypatch, auth_headers, str(uuid.uuid4()))
    assert response.status_code == 200
    assert response.json()["assistant_message_id"] is not None


def test_query_omits_assistant_message_id_when_stateless(auth_headers):
    response = client.post("/generation/query", json={"query": "anything"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["assistant_message_id"] is None


def test_set_message_feedback_returns_204_and_shows_in_history(monkeypatch, auth_headers):
    import uuid

    conversation_id = str(uuid.uuid4())
    query_response = _post_query_with_conversation(monkeypatch, auth_headers, conversation_id)
    message_id = query_response.json()["assistant_message_id"]

    response = client.put(
        f"/conversations/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers=auth_headers,
    )
    assert response.status_code == 204

    history = client.get(f"/conversations/{conversation_id}", headers=auth_headers)
    assistant_message = next(m for m in history.json()["messages"] if m["role"] == "assistant")
    assert assistant_message["feedback"] == "up"


def test_set_message_feedback_can_switch_rating(monkeypatch, auth_headers):
    import uuid

    conversation_id = str(uuid.uuid4())
    query_response = _post_query_with_conversation(monkeypatch, auth_headers, conversation_id)
    message_id = query_response.json()["assistant_message_id"]

    client.put(
        f"/conversations/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers=auth_headers,
    )
    response = client.put(
        f"/conversations/messages/{message_id}/feedback",
        json={"rating": "down"},
        headers=auth_headers,
    )
    assert response.status_code == 204

    history = client.get(f"/conversations/{conversation_id}", headers=auth_headers)
    assistant_message = next(m for m in history.json()["messages"] if m["role"] == "assistant")
    assert assistant_message["feedback"] == "down"


def test_clear_message_feedback_returns_204_and_clears_it(monkeypatch, auth_headers):
    import uuid

    conversation_id = str(uuid.uuid4())
    query_response = _post_query_with_conversation(monkeypatch, auth_headers, conversation_id)
    message_id = query_response.json()["assistant_message_id"]

    client.put(
        f"/conversations/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers=auth_headers,
    )
    response = client.delete(f"/conversations/messages/{message_id}/feedback", headers=auth_headers)
    assert response.status_code == 204

    history = client.get(f"/conversations/{conversation_id}", headers=auth_headers)
    assistant_message = next(m for m in history.json()["messages"] if m["role"] == "assistant")
    assert assistant_message["feedback"] is None


def test_set_message_feedback_404_for_unknown_message(auth_headers):
    import uuid

    response = client.put(
        f"/conversations/messages/{uuid.uuid4()}/feedback",
        json={"rating": "up"},
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_set_message_feedback_404_for_another_users_message(monkeypatch):
    import uuid

    headers_a = register_and_login(client, "generation-feedback-owner-a")
    headers_b = register_and_login(client, "generation-feedback-owner-b")
    conversation_id = str(uuid.uuid4())
    query_response = _post_query_with_conversation(monkeypatch, headers_a, conversation_id)
    message_id = query_response.json()["assistant_message_id"]

    response = client.put(
        f"/conversations/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers=headers_b,
    )
    assert response.status_code == 404


def test_set_message_feedback_rejects_invalid_rating(monkeypatch, auth_headers):
    import uuid

    conversation_id = str(uuid.uuid4())
    query_response = _post_query_with_conversation(monkeypatch, auth_headers, conversation_id)
    message_id = query_response.json()["assistant_message_id"]

    response = client.put(
        f"/conversations/messages/{message_id}/feedback",
        json={"rating": "sideways"},
        headers=auth_headers,
    )
    assert response.status_code == 422
