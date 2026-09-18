import uuid

import pytest

from app.core.db import get_session_factory
from app.generation.config import GenerationSettings
from app.generation.repository import append_message, get_or_create_conversation, get_recent_messages
from app.generation.service import (
    GREETING_ANSWER,
    NO_CONTEXT_ANSWER,
    ConversationAccessDeniedError,
    generate,
    generate_stream,
    get_conversation_history,
    list_conversations,
)
from app.retrieval.schemas import RetrievedChunk

_TEST_OWNER_ID = uuid.uuid4()
_OTHER_OWNER_ID = uuid.uuid4()


def _ensure_owner(session, owner_id):
    from app.auth.models import UserRecord

    if session.get(UserRecord, owner_id) is None:
        session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.flush()


def _ensure_test_owner(session):
    _ensure_owner(session, _TEST_OWNER_ID)


def _chunk(chunk_id):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=f"text for {chunk_id}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )


class _FakeLLMClient:
    def __init__(self, answer):
        self._answer = answer
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._answer


class _FakeStreamingLLMClient:
    def __init__(self, chunks, rewritten_query=None):
        self._chunks = chunks
        self._rewritten_query = rewritten_query
        self.stream_calls = []
        self.generate_calls = []

    def generate(self, system_prompt, user_prompt):
        self.generate_calls.append((system_prompt, user_prompt))
        return self._rewritten_query or "unused"

    def generate_stream(self, system_prompt, user_prompt):
        self.stream_calls.append((system_prompt, user_prompt))
        yield from self._chunks


def test_generate_short_circuits_on_empty_retrieval(monkeypatch):
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeLLMClient("should not be used")

    response = generate("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert response.answer == NO_CONTEXT_ANSWER
    assert response.citations == []
    assert fake_llm.calls == []


@pytest.mark.parametrize(
    "greeting", ["hi", "Hello", "hey!", "hello.", "  hi  ", "howdy?", "hello there", "hi team!"]
)
def test_generate_short_circuits_on_plain_greeting_without_retrieval_or_llm_call(
    monkeypatch, greeting
):
    def _fail_if_called(*a, **k):
        raise AssertionError("retrieval must not run for a plain greeting")

    monkeypatch.setattr("app.generation.service.retrieval_search", _fail_if_called)
    fake_llm = _FakeLLMClient("should not be used")

    response = generate(greeting, top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert response.answer == GREETING_ANSWER
    assert response.citations == []
    assert fake_llm.calls == []


def test_generate_does_not_treat_a_greeting_prefixed_real_question_as_a_greeting(monkeypatch):
    chunks = [_chunk("c1")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1]")

    response = generate(
        "hi, what does the document say about pricing?",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        llm_client=fake_llm,
    )

    assert response.answer == "the answer [1]"
    assert len(fake_llm.calls) == 1


def test_generate_builds_prompt_and_returns_citations(monkeypatch):
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1][2]")

    response = generate(
        "what is X?",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        rerank=True,
        expand_sections=False,
        settings=GenerationSettings(),
        llm_client=fake_llm,
    )

    assert response.answer == "the answer [1][2]"
    assert [c.chunk_id for c in response.citations] == ["c1", "c2"]
    assert all(c.reranked is True for c in response.citations)
    assert len(fake_llm.calls) == 1
    system_prompt, user_prompt = fake_llm.calls[0]
    assert "[1]" in user_prompt and "[2]" in user_prompt
    assert "cite sources inline" in system_prompt.lower()


def test_generate_only_returns_citations_the_answer_actually_references(monkeypatch):
    # ERP-055: c2 was retrieved and included in the prompt's context window, but the model
    # never cited it -- it must not appear in the response's citations.
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1]")

    response = generate("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert [c.chunk_id for c in response.citations] == ["c1"]
    assert response.citations[0].reranked is False


def test_generate_returns_no_citations_when_answer_cites_nothing(monkeypatch):
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer, no citations here")

    response = generate("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert response.citations == []


def test_generate_ignores_a_hallucinated_out_of_range_citation_marker(monkeypatch):
    chunks = [_chunk("c1")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1][5]")

    response = generate("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert [c.chunk_id for c in response.citations] == ["c1"]


def test_generate_parses_comma_separated_citations_in_one_bracket(monkeypatch):
    # ERP-065: a model that writes "[1, 2]" instead of "[1][2]" must still be parsed correctly.
    chunks = [_chunk("c1"), _chunk("c2"), _chunk("c3")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1, 2]")

    response = generate("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert {c.chunk_id for c in response.citations} == {"c1", "c2"}


def test_generate_parses_mixed_single_and_comma_separated_citations(monkeypatch):
    chunks = [_chunk("c1"), _chunk("c2"), _chunk("c3")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1][2, 3]")

    response = generate("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm)

    assert {c.chunk_id for c in response.citations} == {"c1", "c2", "c3"}


def test_generate_passes_retrieval_params_through(monkeypatch):
    captured = {}

    def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False):
        captured["args"] = (query, top_k, owner_id, rerank, expand_sections)
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    fake_llm = _FakeLLMClient("answer")

    generate("q", top_k=7, owner_id=_TEST_OWNER_ID, rerank=True, expand_sections=True, llm_client=fake_llm)

    assert captured["args"] == ("q", 7, _TEST_OWNER_ID, True, True)


def test_generate_with_new_conversation_id_creates_conversation_and_persists_turns(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    chunks = [_chunk("c1")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1]")

    response = generate(
        "what is X?", top_k=5, owner_id=_TEST_OWNER_ID, conversation_id=conversation_id, llm_client=fake_llm
    )

    assert response.conversation_id == conversation_id
    assert response.answer == "the answer [1]"

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "what is X?"
    assert messages[1].content == "the answer [1]"


def test_generate_first_turn_of_conversation_does_not_call_rewrite(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")]
    )
    rewrite_calls = []
    monkeypatch.setattr(
        "app.generation.service.rewrite_query",
        lambda *a, **k: rewrite_calls.append(a) or "should not be reached",
    )
    fake_llm = _FakeLLMClient("answer")

    generate(
        "what is X?", top_k=5, owner_id=_TEST_OWNER_ID, conversation_id=conversation_id, llm_client=fake_llm
    )

    assert rewrite_calls == []


def test_generate_second_turn_rewrites_query_using_history(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        from app.generation.repository import append_message

        append_message(session, conversation_id, "user", "what is the deployment process?")
        append_message(session, conversation_id, "assistant", "it has three steps.")
        session.commit()

    captured_retrieval_query = {}

    def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False):
        captured_retrieval_query["query"] = query
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    monkeypatch.setattr(
        "app.generation.service.rewrite_query",
        lambda query, history, llm_client: "what is the second step in the deployment process?",
    )
    fake_llm = _FakeLLMClient("the second step is test.")

    response = generate(
        "what about the second one?",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        conversation_id=conversation_id,
        llm_client=fake_llm,
    )

    assert captured_retrieval_query["query"] == "what is the second step in the deployment process?"
    assert response.conversation_id == conversation_id

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == [
        "what is the deployment process?",
        "it has three steps.",
        "what about the second one?",
        "the second step is test.",
    ]


def test_generate_conversation_rewrite_failure_propagates_and_commits_nothing(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        from app.generation.repository import append_message

        append_message(session, conversation_id, "user", "first question")
        append_message(session, conversation_id, "assistant", "first answer")
        session.commit()

    def _raise_rewrite(query, history, llm_client):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("app.generation.service.rewrite_query", _raise_rewrite)
    monkeypatch.setattr(
        "app.generation.service.retrieval_search",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retrieval should not run")),
    )
    fake_llm = _FakeLLMClient("should not be reached")

    try:
        generate(
            "a follow-up",
            top_k=5,
            owner_id=_TEST_OWNER_ID,
            conversation_id=conversation_id,
            llm_client=fake_llm,
        )
        raise AssertionError("expected RuntimeError to propagate")
    except RuntimeError as exc:
        assert str(exc) == "ollama unreachable"

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == ["first question", "first answer"]


def test_generate_conversation_short_circuit_still_persists_turns(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeLLMClient("should not be used")

    response = generate(
        "unanswerable question",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        conversation_id=conversation_id,
        llm_client=fake_llm,
    )

    assert response.answer == NO_CONTEXT_ANSWER
    assert fake_llm.calls == []

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == NO_CONTEXT_ANSWER


def test_generate_raises_access_denied_for_other_owners_conversation(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_owner(session, _OTHER_OWNER_ID)
        get_or_create_conversation(session, conversation_id, _OTHER_OWNER_ID)
        session.commit()

    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    def _fail_search(*a, **k):
        raise AssertionError("retrieval should not run when access is denied")

    monkeypatch.setattr("app.generation.service.retrieval_search", _fail_search)
    fake_llm = _FakeLLMClient("should not be used")

    try:
        generate(
            "what is X?",
            top_k=5,
            owner_id=_TEST_OWNER_ID,
            conversation_id=conversation_id,
            llm_client=fake_llm,
        )
        raise AssertionError("expected ConversationAccessDeniedError")
    except ConversationAccessDeniedError:
        pass

    assert fake_llm.calls == []


def test_get_conversation_history_returns_none_for_unknown_id():
    assert get_conversation_history(uuid.uuid4(), _TEST_OWNER_ID) is None


def test_get_conversation_history_returns_all_messages_oldest_first():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "first")
        append_message(session, conversation_id, "assistant", "second")
        session.commit()

    history = get_conversation_history(conversation_id, _TEST_OWNER_ID)

    assert history is not None
    assert history.conversation_id == conversation_id
    assert [m.content for m in history.messages] == ["first", "second"]
    assert [m.role for m in history.messages] == ["user", "assistant"]


def test_generate_stream_stateless_yields_tokens_then_citations_then_done(monkeypatch):
    # Only c1 is cited ("[1]" appears in the streamed answer); c2 was in context but never
    # referenced, so ERP-055 excludes it from the citations event.
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeStreamingLLMClient(["Hello", " world [1]"])

    events = list(generate_stream("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm))

    assert events == [
        ("token", {"text": "Hello"}),
        ("token", {"text": " world [1]"}),
        (
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
                    },
                ]
            },
        ),
        ("done", {"conversation_id": None}),
    ]


def test_generate_stream_stateless_short_circuits_on_empty_retrieval(monkeypatch):
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeStreamingLLMClient(["should not be used"])

    events = list(generate_stream("what is X?", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm))

    assert events == [
        ("token", {"text": NO_CONTEXT_ANSWER}),
        ("citations", {"citations": []}),
        ("done", {"conversation_id": None}),
    ]
    assert fake_llm.stream_calls == []


def test_generate_stream_stateless_short_circuits_on_plain_greeting(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("retrieval must not run for a plain greeting")

    monkeypatch.setattr("app.generation.service.retrieval_search", _fail_if_called)
    fake_llm = _FakeStreamingLLMClient(["should not be used"])

    events = list(generate_stream("hello", top_k=5, owner_id=_TEST_OWNER_ID, llm_client=fake_llm))

    assert events == [
        ("token", {"text": GREETING_ANSWER}),
        ("citations", {"citations": []}),
        ("done", {"conversation_id": None}),
    ]
    assert fake_llm.stream_calls == []


def test_generate_with_conversation_id_short_circuits_on_greeting_and_still_persists(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    def _fail_if_called(*a, **k):
        raise AssertionError("retrieval must not run for a plain greeting")

    monkeypatch.setattr("app.generation.service.retrieval_search", _fail_if_called)
    fake_llm = _FakeLLMClient("should not be used")

    response = generate(
        "hi",
        top_k=5,
        owner_id=_TEST_OWNER_ID,
        conversation_id=conversation_id,
        llm_client=fake_llm,
    )

    assert response.answer == GREETING_ANSWER
    assert response.citations == []
    assert fake_llm.calls == []

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == ["hi", GREETING_ANSWER]


def test_generate_stream_with_conversation_id_persists_after_done(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        session.commit()

    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")])
    fake_llm = _FakeStreamingLLMClient(["the ", "answer"])

    events = list(
        generate_stream(
            "what is X?",
            top_k=5,
            owner_id=_TEST_OWNER_ID,
            conversation_id=conversation_id,
            llm_client=fake_llm,
        )
    )

    assert events[-1][0] == "done"
    assert events[-1][1]["conversation_id"] == str(conversation_id)
    assert isinstance(events[-1][1]["assistant_message_id"], str)
    assert fake_llm.generate_calls == []

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == ["what is X?", "the answer"]


def test_generate_stream_second_turn_rewrites_query_using_history(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "what is the deployment process?")
        append_message(session, conversation_id, "assistant", "it has three steps.")
        session.commit()

    captured_retrieval_query = {}

    def _fake_search(query, top_k, owner_id, rerank=False, expand_sections=False):
        captured_retrieval_query["query"] = query
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    fake_llm = _FakeStreamingLLMClient(
        ["the second step is test."],
        rewritten_query="what is the second step in the deployment process?",
    )

    events = list(
        generate_stream(
            "what about the second one?",
            top_k=5,
            owner_id=_TEST_OWNER_ID,
            conversation_id=conversation_id,
            llm_client=fake_llm,
        )
    )

    assert captured_retrieval_query["query"] == "what is the second step in the deployment process?"
    assert len(fake_llm.generate_calls) == 1
    assert events[-1][0] == "done"
    assert events[-1][1]["conversation_id"] == str(conversation_id)


def test_generate_stream_exception_mid_stream_yields_error_and_persists_nothing(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")])

    class _RaisingLLMClient:
        def generate(self, system_prompt, user_prompt):
            raise AssertionError("rewrite should not run on a conversation's first turn")

        def generate_stream(self, system_prompt, user_prompt):
            yield "partial"
            raise RuntimeError("ollama connection dropped")

    events = list(
        generate_stream(
            "what is X?",
            top_k=5,
            owner_id=_TEST_OWNER_ID,
            conversation_id=conversation_id,
            llm_client=_RaisingLLMClient(),
        )
    )

    assert ("token", {"text": "partial"}) in events
    assert events[-1] == ("error", {"detail": "Generation query failed"})

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert messages == []


def test_list_conversations_returns_newest_first_with_preview():
    owner_id = uuid.uuid4()
    conv_a, conv_b = uuid.uuid4(), uuid.uuid4()

    session_factory = get_session_factory()
    with session_factory() as session:
        from app.auth.models import UserRecord

        session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.commit()

    with session_factory() as session:
        get_or_create_conversation(session, conv_a, owner_id)
        append_message(session, conv_a, "user", "first conversation's opening question")
        session.commit()

    with session_factory() as session:
        get_or_create_conversation(session, conv_b, owner_id)
        append_message(session, conv_b, "user", "second conversation's opening question")
        session.commit()

    response = list_conversations(owner_id)

    assert [c.conversation_id for c in response.conversations] == [conv_b, conv_a]
    assert response.conversations[0].preview == "second conversation's opening question"
    assert response.conversations[1].preview == "first conversation's opening question"


def test_list_conversations_no_conversations_returns_empty_list():
    response = list_conversations(uuid.uuid4())

    assert response.conversations == []
