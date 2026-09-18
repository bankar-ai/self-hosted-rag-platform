import uuid

from app.core.db import get_session_factory
from app.ingestion.models import ChunkRecord, DocumentRecord
from app.ingestion.repository import (
    delete_document,
    get_chunks_by_vector_ids,
    get_sibling_chunks,
    get_vector_ids_for_documents,
    list_documents_for_owner,
    save_document_and_chunks,
    search_chunks_by_text,
)
from app.ingestion.schemas import Chunk

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


def _chunk(
    document_id: str, index: int, text: str | None = None, section_path: list[str] | None = None
) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-{index}",
        document_id=document_id,
        chunk_index=index,
        text=text if text is not None else f"chunk text {index}",
        section_path=section_path if section_path is not None else ["Intro"],
        page_start=1,
        page_end=1,
        char_count=13,
        parser_used="fast",
        source_filename="doc.pdf",
    )


def test_save_document_and_chunks_persists_rows_and_assigns_vector_ids():
    document_id = "doc-repo-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

        assert len(records) == 2
        assert all(isinstance(record.vector_id, int) for record in records)
        assert records[0].vector_id != records[1].vector_id
        assert [r.chunk_id for r in records] == ["doc-repo-test-0", "doc-repo-test-1"]


def test_get_chunks_by_vector_ids_returns_rows_keyed_by_vector_id():
    document_id = "doc-lookup-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()
        vector_ids = [record.vector_id for record in records]

    with session_factory() as session:
        found = get_chunks_by_vector_ids(session, vector_ids, _TEST_OWNER_ID)

        assert set(found.keys()) == set(vector_ids)
        for vector_id, record in found.items():
            assert record.vector_id == vector_id
            assert record.document_id == document_id


def test_get_chunks_by_vector_ids_empty_input_returns_empty_dict():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_chunks_by_vector_ids(session, [], _TEST_OWNER_ID) == {}


def test_get_chunks_by_vector_ids_ignores_unknown_ids():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_chunks_by_vector_ids(session, [999_999_999], _TEST_OWNER_ID) == {}


def test_search_chunks_by_text_ranks_matching_chunk_first():
    document_id = "doc-fts-test"
    chunks = [
        _chunk(document_id, 0, text="giraffes are tall herbivorous mammals from Africa"),
        _chunk(document_id, 1, text="the stock market closed lower on Tuesday"),
    ]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()
        vector_ids = [record.vector_id for record in records]

    with session_factory() as session:
        results = search_chunks_by_text(session, "giraffes Africa", k=5, owner_id=_TEST_OWNER_ID)

    assert results
    assert results[0][0] == vector_ids[0]


def test_search_chunks_by_text_no_match_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert search_chunks_by_text(session, "zzzznonexistentqueryterm", k=5, owner_id=_TEST_OWNER_ID) == []


def test_search_chunks_by_text_empty_query_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert search_chunks_by_text(session, "", k=5, owner_id=_TEST_OWNER_ID) == []


def test_get_sibling_chunks_returns_only_matching_section_ordered_by_chunk_index():
    document_id = "doc-siblings-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 1, section_path=["Chapter 1", "Background"]),
        _chunk(document_id, 2, section_path=["Chapter 1", "Methods"]),
    ]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        siblings = get_sibling_chunks(session, document_id, ["Chapter 1", "Background"])

    assert [chunk.chunk_id for chunk in siblings] == [f"{document_id}-0", f"{document_id}-1"]


def test_get_sibling_chunks_honors_exclude_chunk_ids():
    document_id = "doc-siblings-exclude-test"
    chunks = [
        _chunk(document_id, 0, section_path=["Intro"]),
        _chunk(document_id, 1, section_path=["Intro"]),
    ]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        siblings = get_sibling_chunks(
            session, document_id, ["Intro"], exclude_chunk_ids={f"{document_id}-0"}
        )

    assert [chunk.chunk_id for chunk in siblings] == [f"{document_id}-1"]


def test_get_sibling_chunks_no_matching_section_returns_empty_list():
    document_id = "doc-siblings-empty-test"
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", [_chunk(document_id, 0)], _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        assert get_sibling_chunks(session, document_id, ["Nonexistent Section"]) == []


def test_list_documents_for_owner_returns_newest_first():
    # Each document is saved in its own committed transaction, same as real ingestion jobs
    # (each an independent background task) -- `created_at` is a `server_default=func.now()`
    # column, which is the *transaction's* start time, so two rows saved in one transaction
    # (as separate ingestion jobs never are) would tie and make ordering ambiguous.
    owner_id = uuid.uuid4()
    doc_a, doc_b = f"doc-list-a-{owner_id}", f"doc-list-b-{owner_id}"

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        from app.auth.models import UserRecord

        session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.commit()

    with session_factory() as session:
        save_document_and_chunks(session, doc_a, "a.pdf", [_chunk(doc_a, 0)], owner_id)
        session.commit()

    with session_factory() as session:
        save_document_and_chunks(session, doc_b, "b.pdf", [_chunk(doc_b, 0)], owner_id)
        session.commit()

    with session_factory() as session:
        documents = list_documents_for_owner(session, owner_id)

    assert [d.document_id for d in documents] == [doc_b, doc_a]


def test_list_documents_for_owner_excludes_other_owners_documents():
    owner_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        assert list_documents_for_owner(session, owner_id) == []


def test_delete_document_removes_document_and_chunks_returns_vector_ids():
    document_id = "doc-delete-repo-test"
    chunks = [_chunk(document_id, 0), _chunk(document_id, 1)]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records = save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()
        expected_vector_ids = {r.vector_id for r in records}

    with session_factory() as session:
        vector_ids = delete_document(session, document_id, _TEST_OWNER_ID)
        session.commit()

        assert vector_ids is not None
        assert set(vector_ids) == expected_vector_ids

    with session_factory() as session:
        assert session.get(DocumentRecord, document_id) is None
        assert session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).count() == 0


def test_delete_document_unknown_document_returns_none():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert delete_document(session, "does-not-exist", _TEST_OWNER_ID) is None


def test_delete_document_wrong_owner_returns_none_and_deletes_nothing():
    document_id = "doc-delete-wrong-owner-repo-test"
    other_owner_id = uuid.uuid4()

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        from app.auth.models import UserRecord

        session.add(UserRecord(id=other_owner_id, email=f"{other_owner_id}@test", hashed_password="x"))
        session.flush()
        save_document_and_chunks(session, document_id, "doc.pdf", [_chunk(document_id, 0)], _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        assert delete_document(session, document_id, other_owner_id) is None

    with session_factory() as session:
        assert session.get(DocumentRecord, document_id) is not None


def test_search_chunks_by_text_restricts_to_given_document_ids():
    doc_a, doc_b = f"doc-scope-a-{uuid.uuid4()}", f"doc-scope-b-{uuid.uuid4()}"
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(
            session, doc_a, "a.pdf", [_chunk(doc_a, 0, text="giraffes are tall mammals")], _TEST_OWNER_ID
        )
        save_document_and_chunks(
            session, doc_b, "b.pdf", [_chunk(doc_b, 0, text="giraffes live in Africa too")], _TEST_OWNER_ID
        )
        session.commit()

    with session_factory() as session:
        results = search_chunks_by_text(
            session, "giraffes", k=5, owner_id=_TEST_OWNER_ID, document_ids=[doc_a]
        )

    with session_factory() as session:
        chunk_a = get_chunks_by_vector_ids(session, [vid for vid, _ in results], _TEST_OWNER_ID)
        assert all(row.document_id == doc_a for row in chunk_a.values())
        assert len(results) == 1


def test_search_chunks_by_text_document_ids_none_is_unrestricted():
    document_id = f"doc-scope-unrestricted-{uuid.uuid4()}"
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        chunk = _chunk(document_id, 0, text="wombats burrow at dusk")
        save_document_and_chunks(session, document_id, "doc.pdf", [chunk], _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        results = search_chunks_by_text(
            session, "wombats", k=5, owner_id=_TEST_OWNER_ID, document_ids=None
        )

    assert len(results) == 1


def test_search_chunks_by_text_empty_document_ids_returns_nothing():
    document_id = f"doc-scope-empty-{uuid.uuid4()}"
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        chunk = _chunk(document_id, 0, text="pangolins eat ants")
        save_document_and_chunks(session, document_id, "doc.pdf", [chunk], _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        results = search_chunks_by_text(
            session, "pangolins", k=5, owner_id=_TEST_OWNER_ID, document_ids=[]
        )

    assert results == []


def test_get_vector_ids_for_documents_returns_only_requested_documents():
    doc_a, doc_b = f"doc-vecids-a-{uuid.uuid4()}", f"doc-vecids-b-{uuid.uuid4()}"
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        records_a = save_document_and_chunks(session, doc_a, "a.pdf", [_chunk(doc_a, 0)], _TEST_OWNER_ID)
        save_document_and_chunks(session, doc_b, "b.pdf", [_chunk(doc_b, 0)], _TEST_OWNER_ID)
        session.commit()
        expected = {r.vector_id for r in records_a}

    with session_factory() as session:
        vector_ids = get_vector_ids_for_documents(session, _TEST_OWNER_ID, [doc_a])

    assert set(vector_ids) == expected


def test_get_vector_ids_for_documents_excludes_another_owners_document():
    document_id = f"doc-vecids-other-owner-{uuid.uuid4()}"
    other_owner_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", [_chunk(document_id, 0)], _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        vector_ids = get_vector_ids_for_documents(session, other_owner_id, [document_id])

    assert vector_ids == []


def test_get_vector_ids_for_documents_empty_input_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_vector_ids_for_documents(session, _TEST_OWNER_ID, []) == []
