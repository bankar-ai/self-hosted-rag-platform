import uuid

import pytest
from pydantic import ValidationError

from app.generation.schemas import Citation, GenerationQuery, GenerationResponse


def test_generation_query_defaults():
    query = GenerationQuery(query="hello")
    assert query.top_k == 5
    assert query.rerank is False
    assert query.expand_sections is False


def test_generation_query_rejects_empty_query():
    with pytest.raises(ValidationError):
        GenerationQuery(query="")


def test_generation_query_rejects_top_k_out_of_bounds():
    with pytest.raises(ValidationError):
        GenerationQuery(query="hello", top_k=0)


def test_generation_response_round_trip():
    citation = Citation(
        chunk_id="c1",
        document_id="doc-1",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
        reranked=False,
    )
    response = GenerationResponse(answer="the answer [1]", citations=[citation])

    assert response.model_dump()["citations"][0]["chunk_id"] == "c1"


def test_generation_query_conversation_id_defaults_to_none():
    query = GenerationQuery(query="hello")
    assert query.conversation_id is None


def test_generation_query_accepts_conversation_id():
    conversation_id = uuid.uuid4()
    query = GenerationQuery(query="hello", conversation_id=conversation_id)
    assert query.conversation_id == conversation_id


def test_generation_response_conversation_id_defaults_to_none():
    response = GenerationResponse(answer="hi", citations=[])
    assert response.conversation_id is None


def test_generation_response_accepts_conversation_id():
    conversation_id = uuid.uuid4()
    response = GenerationResponse(answer="hi", citations=[], conversation_id=conversation_id)
    assert response.conversation_id == conversation_id


def test_conversation_turn_round_trip():
    from app.generation.schemas import ConversationTurn

    turn = ConversationTurn(role="user", content="hello")
    assert turn.model_dump() == {"role": "user", "content": "hello"}


def test_message_round_trip():
    from datetime import datetime

    from app.generation.schemas import Message

    now = datetime(2026, 9, 1, 12, 0, 0)
    message_id = uuid.uuid4()
    message = Message(id=message_id, role="user", content="hello", created_at=now)
    assert message.model_dump() == {
        "id": message_id,
        "role": "user",
        "content": "hello",
        "created_at": now,
        "feedback": None,
    }


def test_conversation_history_response_round_trip():
    from datetime import datetime

    from app.generation.schemas import ConversationHistoryResponse, Message

    conversation_id = uuid.uuid4()
    message = Message(
        id=uuid.uuid4(), role="user", content="hello", created_at=datetime(2026, 9, 1, 12, 0, 0)
    )
    response = ConversationHistoryResponse(conversation_id=conversation_id, messages=[message])

    assert response.conversation_id == conversation_id
    assert response.messages == [message]


def test_conversation_history_response_empty_messages():
    from app.generation.schemas import ConversationHistoryResponse

    response = ConversationHistoryResponse(conversation_id=uuid.uuid4(), messages=[])
    assert response.messages == []
