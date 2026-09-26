import uuid

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.evaluation.models import EvaluationRunRecord, GenerationEvaluationRunRecord
from app.evaluation.repository import (
    cleanup_eval_data,
    get_preceding_user_message,
    get_unsampled_assistant_messages,
    save_evaluation_run,
    save_generation_evaluation_run,
    save_production_sample_score,
)
from app.evaluation.schemas import (
    EvaluationSummary,
    GenerationEvaluationSummary,
    GenerationQueryResult,
    ProductionSampleResult,
    QueryResult,
)
from app.generation.models import ConversationMessageRecord
from app.generation.repository import append_message, get_or_create_conversation
from app.ingestion.repository import get_chunks_by_vector_ids, save_document_and_chunks
from app.ingestion.schemas import Chunk


def _summary() -> EvaluationSummary:
    return EvaluationSummary(
        top_k=3,
        num_queries=1,
        mean_precision=0.5,
        mean_recall=1.0,
        mrr=1.0,
        per_query=[
            QueryResult(
                query="q",
                precision=0.5,
                recall=1.0,
                reciprocal_rank=1.0,
                retrieved_chunk_ids=["a-0"],
                relevant_chunk_ids=["a-0"],
            )
        ],
    )


def test_save_evaluation_run_persists_summary_fields():
    session_factory = get_session_factory()
    with session_factory() as session:
        record = save_evaluation_run(session, _summary())
        session.commit()

        assert record.top_k == 3
        assert record.num_queries == 1
        assert record.mean_precision_at_k == 0.5
        assert record.mean_recall_at_k == 1.0
        assert record.mrr == 1.0
        assert record.details[0]["query"] == "q"

        record_id = record.id
        session.query(EvaluationRunRecord).filter(EvaluationRunRecord.id == record_id).delete()
        session.commit()


def test_cleanup_eval_data_removes_chunks_documents_and_user():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"eval-cleanup-{uuid.uuid4()}@test", "x")
        session.flush()
        document_id = str(uuid.uuid4())
        chunk = Chunk(
            chunk_id=f"{document_id}-0",
            document_id=document_id,
            chunk_index=0,
            text="text",
            section_path=["A"],
            page_start=1,
            page_end=1,
            char_count=4,
            parser_used="fast",
            source_filename="doc.pdf",
        )
        records = save_document_and_chunks(session, document_id, "doc.pdf", [chunk], user.id)
        vector_id = records[0].vector_id
        session.commit()

        cleanup_eval_data(session, [document_id], user.id)
        session.commit()

        assert get_chunks_by_vector_ids(session, [vector_id], user.id) == {}


def _generation_summary() -> GenerationEvaluationSummary:
    return GenerationEvaluationSummary(
        judge="ragas",
        num_queries=1,
        mean_faithfulness=0.9,
        mean_answer_relevancy=0.8,
        mean_context_precision=0.7,
        per_query=[
            GenerationQueryResult(
                query="q",
                answer="a",
                faithfulness=0.9,
                answer_relevancy=0.8,
                context_precision=0.7,
            )
        ],
    )


def test_save_generation_evaluation_run_persists_summary_fields():
    session_factory = get_session_factory()
    with session_factory() as session:
        record = save_generation_evaluation_run(session, _generation_summary())
        session.commit()

        assert record.judge == "ragas"
        assert record.num_queries == 1
        assert record.mean_faithfulness == 0.9
        assert record.mean_answer_relevancy == 0.8
        assert record.mean_context_precision == 0.7
        assert record.details[0]["query"] == "q"

        record_id = record.id
        session.query(GenerationEvaluationRunRecord).filter(
            GenerationEvaluationRunRecord.id == record_id
        ).delete()
        session.commit()


def _build_conversation_turn(
    session, owner_id: uuid.UUID, question: str = "what is X?", answer: str = "X is Y [1]"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create one conversation with a user turn followed by an assistant turn (ERP-097 fixtures).

    Returns (conversation_id, user_message_id, assistant_message_id).
    """
    conversation_id = uuid.uuid4()
    get_or_create_conversation(session, conversation_id, owner_id)
    user_message = append_message(session, conversation_id, "user", question)
    assistant_message = append_message(
        session, conversation_id, "assistant", answer, citations=[{"chunk_id": "c1"}]
    )
    session.flush()
    return conversation_id, user_message.id, assistant_message.id


def test_get_unsampled_assistant_messages_excludes_already_scored():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"unsampled-{uuid.uuid4()}@test", "x")
        session.flush()
        _, _, unscored_id = _build_conversation_turn(session, user.id)
        conv_b, _, already_scored_id = _build_conversation_turn(session, user.id)
        save_production_sample_score(
            session,
            ProductionSampleResult(
                message_id=already_scored_id,
                conversation_id=conv_b,
                query="what is X?",
                judge="ollama",
                faithfulness=0.9,
                answer_relevancy=0.8,
                context_precision=0.7,
            ),
        )
        session.commit()

        found_ids = {m.id for m in get_unsampled_assistant_messages(session, limit=50)}

        assert unscored_id in found_ids
        assert already_scored_id not in found_ids


def test_get_unsampled_assistant_messages_excludes_user_role_messages():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"unsampled-userrole-{uuid.uuid4()}@test", "x")
        session.flush()
        _, user_message_id, _ = _build_conversation_turn(session, user.id)
        session.commit()

        found_ids = {m.id for m in get_unsampled_assistant_messages(session, limit=1000)}

        assert user_message_id not in found_ids


def test_get_preceding_user_message_returns_the_matching_question():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"preceding-{uuid.uuid4()}@test", "x")
        session.flush()
        _, user_message_id, assistant_message_id = _build_conversation_turn(
            session, user.id, question="what is the answer to everything?"
        )
        session.commit()

        assistant_message = session.get(ConversationMessageRecord, assistant_message_id)
        preceding = get_preceding_user_message(session, assistant_message)

        assert preceding is not None
        assert preceding.id == user_message_id
        assert preceding.content == "what is the answer to everything?"


def test_get_preceding_user_message_returns_none_when_no_user_turn_precedes_it():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"preceding-none-{uuid.uuid4()}@test", "x")
        session.flush()
        conversation_id = uuid.uuid4()
        get_or_create_conversation(session, conversation_id, user.id)
        # An assistant message with no preceding user message at all (shouldn't happen via the
        # real app, but the lookup must not crash if it somehow does).
        orphan_assistant = append_message(session, conversation_id, "assistant", "orphan answer")
        session.commit()

        assert get_preceding_user_message(session, orphan_assistant) is None


def test_save_production_sample_score_persists_scored_result():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"save-score-{uuid.uuid4()}@test", "x")
        session.flush()
        conversation_id, _, assistant_message_id = _build_conversation_turn(session, user.id)

        record = save_production_sample_score(
            session,
            ProductionSampleResult(
                message_id=assistant_message_id,
                conversation_id=conversation_id,
                query="what is X?",
                judge="ollama",
                faithfulness=0.9,
                answer_relevancy=0.8,
                context_precision=0.7,
            ),
        )
        session.commit()

        assert record.message_id == assistant_message_id
        assert record.skipped is False
        assert record.faithfulness == 0.9


def test_save_production_sample_score_persists_a_skipped_result():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"save-score-skip-{uuid.uuid4()}@test", "x")
        session.flush()
        conversation_id, _, assistant_message_id = _build_conversation_turn(session, user.id)

        record = save_production_sample_score(
            session,
            ProductionSampleResult(
                message_id=assistant_message_id,
                conversation_id=conversation_id,
                query="what is X?",
                judge="ollama",
                skipped=True,
                skip_reason="no resolvable context",
            ),
        )
        session.commit()

        assert record.skipped is True
        assert record.skip_reason == "no resolvable context"
        assert record.faithfulness is None
