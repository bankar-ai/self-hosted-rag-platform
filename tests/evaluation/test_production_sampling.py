import uuid

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.evaluation.production_sampling import run_production_sampling
from app.evaluation.schemas import GenerationScores
from app.generation.repository import append_message, get_or_create_conversation
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk


class _FixedFakeJudge:
    def score(self, user_input, response, retrieved_contexts):
        return GenerationScores(faithfulness=0.9, answer_relevancy=0.8, context_precision=0.7)


def _make_chunk(document_id: str, chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        text=text,
        section_path=["A"],
        page_start=1,
        page_end=1,
        char_count=len(text),
        parser_used="fast",
        source_filename="doc.pdf",
    )


def test_run_production_sampling_scores_a_message_with_resolvable_citations():
    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"prod-sample-{uuid.uuid4()}@test", "x")
        session.flush()
        document_id = str(uuid.uuid4())
        chunk_id = f"{document_id}-0"
        chunk = _make_chunk(document_id, chunk_id, "Paris is the capital.")
        save_document_and_chunks(session, document_id, "doc.pdf", [chunk], owner.id)
        conversation_id = uuid.uuid4()
        get_or_create_conversation(session, conversation_id, owner.id)
        append_message(session, conversation_id, "user", "what is the capital of France?")
        append_message(
            session,
            conversation_id,
            "assistant",
            "Paris [1]",
            citations=[{"chunk_id": chunk_id, "marker": 1}],
        )
        session.commit()

    summary = run_production_sampling(judge=_FixedFakeJudge(), limit=50, session_factory=session_factory)

    assert summary.num_candidates == 1
    assert summary.num_scored == 1
    assert summary.num_skipped == 0

    with session_factory() as session:
        from app.evaluation.models import ProductionSampleScoreRecord

        record = (
            session.query(ProductionSampleScoreRecord)
            .filter(ProductionSampleScoreRecord.query == "what is the capital of France?")
            .one()
        )
        assert record.source_documents == ["doc.pdf"]
    assert summary.mean_faithfulness == 0.9


def test_run_production_sampling_skips_a_message_with_only_deleted_citations():
    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"prod-sample-deleted-{uuid.uuid4()}@test", "x")
        session.flush()
        conversation_id = uuid.uuid4()
        get_or_create_conversation(session, conversation_id, owner.id)
        append_message(session, conversation_id, "user", "what is X?")
        append_message(
            session,
            conversation_id,
            "assistant",
            "X is Y [1]",
            citations=[{"chunk_id": "chunk-that-no-longer-exists", "marker": 1}],
        )
        session.commit()

    summary = run_production_sampling(judge=_FixedFakeJudge(), limit=50, session_factory=session_factory)

    assert summary.num_candidates == 1
    assert summary.num_scored == 0
    assert summary.num_skipped == 1


def test_run_production_sampling_skips_a_message_with_no_citations_at_all():
    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"prod-sample-nocite-{uuid.uuid4()}@test", "x")
        session.flush()
        conversation_id = uuid.uuid4()
        get_or_create_conversation(session, conversation_id, owner.id)
        append_message(session, conversation_id, "user", "hi")
        append_message(session, conversation_id, "assistant", "Hello! How can I help?", citations=[])
        session.commit()

    summary = run_production_sampling(judge=_FixedFakeJudge(), limit=50, session_factory=session_factory)

    assert summary.num_candidates == 1
    assert summary.num_scored == 0
    assert summary.num_skipped == 1


def test_run_production_sampling_never_rescoring_an_already_sampled_message():
    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"prod-sample-once-{uuid.uuid4()}@test", "x")
        session.flush()
        conversation_id = uuid.uuid4()
        get_or_create_conversation(session, conversation_id, owner.id)
        append_message(session, conversation_id, "user", "hi")
        append_message(session, conversation_id, "assistant", "Hello!", citations=[])
        session.commit()

    first = run_production_sampling(judge=_FixedFakeJudge(), limit=50, session_factory=session_factory)
    second = run_production_sampling(judge=_FixedFakeJudge(), limit=50, session_factory=session_factory)

    assert first.num_candidates == 1
    assert second.num_candidates == 0


def test_run_production_sampling_respects_limit():
    session_factory = get_session_factory()
    with session_factory() as session:
        owner = create_user(session, f"prod-sample-limit-{uuid.uuid4()}@test", "x")
        session.flush()
        for _ in range(3):
            conversation_id = uuid.uuid4()
            get_or_create_conversation(session, conversation_id, owner.id)
            append_message(session, conversation_id, "user", "hi")
            append_message(session, conversation_id, "assistant", "Hello!", citations=[])
        session.commit()

    summary = run_production_sampling(judge=_FixedFakeJudge(), limit=2, session_factory=session_factory)

    assert summary.num_candidates == 2


def test_run_production_sampling_persists_prior_scores_when_a_later_one_crashes():
    """ERP-105: a mid-batch crash must not lose already-scored messages before it."""

    class _CrashesOnSecondCallJudge:
        def __init__(self):
            self.calls = 0

        def score(self, user_input, response, retrieved_contexts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated judge failure")
            return GenerationScores(faithfulness=0.9, answer_relevancy=0.8, context_precision=0.7)

    session_factory = get_session_factory()
    message_ids = []
    with session_factory() as session:
        owner = create_user(session, f"prod-sample-crash-{uuid.uuid4()}@test", "x")
        session.flush()
        for i in range(2):
            document_id = str(uuid.uuid4())
            chunk_id = f"{document_id}-0"
            chunk = _make_chunk(document_id, chunk_id, f"context {i}")
            save_document_and_chunks(session, document_id, "doc.pdf", [chunk], owner.id)
            conversation_id = uuid.uuid4()
            get_or_create_conversation(session, conversation_id, owner.id)
            append_message(session, conversation_id, "user", f"question {i}")
            assistant_message = append_message(
                session, conversation_id, "assistant", f"answer {i}", citations=[{"chunk_id": chunk_id}]
            )
            message_ids.append(assistant_message.id)
        session.commit()

    try:
        run_production_sampling(
            judge=_CrashesOnSecondCallJudge(), limit=50, session_factory=session_factory
        )
    except RuntimeError:
        pass

    with session_factory() as session:
        from app.evaluation.models import ProductionSampleScoreRecord

        rows = (
            session.query(ProductionSampleScoreRecord)
            .filter(ProductionSampleScoreRecord.message_id == message_ids[0])
            .all()
        )
        assert len(rows) == 1
        assert rows[0].faithfulness == 0.9
