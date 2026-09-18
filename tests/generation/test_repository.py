import uuid

from app.core.db import get_session_factory
from app.generation.repository import (
    append_message,
    clear_message_feedback,
    get_all_messages,
    get_conversation,
    get_conversation_owner_id,
    get_feedback_for_messages,
    get_first_user_messages,
    get_or_create_conversation,
    get_recent_messages,
    list_conversations_for_owner,
    rename_conversation,
    set_message_feedback,
    title_exists_for_owner,
)

_TEST_OWNER_ID = uuid.uuid4()


def _ensure_test_owner(session):
    from app.auth.models import UserRecord

    if session.get(UserRecord, _TEST_OWNER_ID) is None:
        session.add(UserRecord(id=_TEST_OWNER_ID, email=f"{_TEST_OWNER_ID}@test", hashed_password="x"))
        session.flush()


def test_get_or_create_conversation_creates_new_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        conversation = get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

        assert conversation.id == conversation_id


def test_get_or_create_conversation_returns_existing_row_without_duplicating():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        conversation = get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

        assert conversation.id == conversation_id


def test_append_message_persists_role_and_content():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        message = append_message(session, conversation_id, "user", "hello there")
        session.commit()

        assert message.role == "user"
        assert message.content == "hello there"
        assert message.conversation_id == conversation_id


def test_get_recent_messages_returns_oldest_first():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "first")
        append_message(session, conversation_id, "assistant", "second")
        append_message(session, conversation_id, "user", "third")
        session.commit()

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)

        assert [m.content for m in messages] == ["first", "second", "third"]


def test_get_recent_messages_respects_limit_keeping_most_recent():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "first")
        append_message(session, conversation_id, "assistant", "second")
        append_message(session, conversation_id, "user", "third")
        session.commit()

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=2)

        assert [m.content for m in messages] == ["second", "third"]


def test_get_recent_messages_unknown_conversation_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_recent_messages(session, uuid.uuid4(), limit=10) == []


def test_get_conversation_returns_existing_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        conversation = get_conversation(session, conversation_id, _TEST_OWNER_ID)

        assert conversation is not None
        assert conversation.id == conversation_id


def test_get_conversation_unknown_id_returns_none():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_conversation(session, uuid.uuid4(), _TEST_OWNER_ID) is None


def test_get_conversation_does_not_create_a_row():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_conversation(session, conversation_id, _TEST_OWNER_ID)

    with session_factory() as session:
        assert get_conversation(session, conversation_id, _TEST_OWNER_ID) is None


def test_get_all_messages_returns_every_message_oldest_first_no_limit():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        for i in range(12):
            append_message(session, conversation_id, "user", f"message {i}")
        session.commit()

    with session_factory() as session:
        messages = get_all_messages(session, conversation_id)

        assert [m.content for m in messages] == [f"message {i}" for i in range(12)]


def test_get_all_messages_unknown_conversation_returns_empty_list():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_all_messages(session, uuid.uuid4()) == []


def test_get_conversation_owner_id_returns_none_for_unknown_id():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_conversation_owner_id(session, uuid.uuid4()) is None


def test_get_conversation_owner_id_returns_owner_for_existing_conversation():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        assert get_conversation_owner_id(session, conversation_id) == _TEST_OWNER_ID


def test_list_conversations_for_owner_returns_newest_first():
    # Each conversation is created and committed in its own transaction, same as real usage
    # (separate requests) -- `created_at` is the *transaction's* start time, so two rows
    # created in one transaction would tie and make ordering ambiguous.
    owner_id = uuid.uuid4()
    conv_a, conv_b = uuid.uuid4(), uuid.uuid4()

    session_factory = get_session_factory()
    with session_factory() as session:
        from app.auth.models import UserRecord

        session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.commit()

    with session_factory() as session:
        get_or_create_conversation(session, conv_a, owner_id)
        session.commit()

    with session_factory() as session:
        get_or_create_conversation(session, conv_b, owner_id)
        session.commit()

    with session_factory() as session:
        conversations = list_conversations_for_owner(session, owner_id)

    assert [c.id for c in conversations] == [conv_b, conv_a]


def test_list_conversations_for_owner_excludes_other_owners():
    owner_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        assert list_conversations_for_owner(session, owner_id) == []


def test_get_first_user_messages_returns_earliest_user_message_per_conversation():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        append_message(session, conversation_id, "user", "first question")
        append_message(session, conversation_id, "assistant", "an answer")
        append_message(session, conversation_id, "user", "follow-up question")
        session.commit()

    with session_factory() as session:
        previews = get_first_user_messages(session, [conversation_id])

    assert previews == {conversation_id: "first question"}


def test_get_first_user_messages_empty_input_returns_empty_dict():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_first_user_messages(session, []) == {}


def test_get_first_user_messages_conversation_with_no_messages_is_absent():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        assert get_first_user_messages(session, [conversation_id]) == {}


def test_rename_conversation_sets_title():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        renamed = rename_conversation(session, conversation_id, _TEST_OWNER_ID, "My renamed chat")
        session.commit()
        assert renamed is True

    with session_factory() as session:
        from app.generation.models import ConversationRecord

        conversation = session.get(ConversationRecord, conversation_id)
        assert conversation.title == "My renamed chat"


def test_rename_conversation_unknown_id_returns_false():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert rename_conversation(session, uuid.uuid4(), _TEST_OWNER_ID, "title") is False


def test_rename_conversation_wrong_owner_returns_false_and_does_not_rename():
    conversation_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        from app.auth.models import UserRecord

        session.add(UserRecord(id=other_owner_id, email=f"{other_owner_id}@test", hashed_password="x"))
        session.flush()
        get_or_create_conversation(session, conversation_id, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        assert rename_conversation(session, conversation_id, other_owner_id, "hijacked") is False

    with session_factory() as session:
        from app.generation.models import ConversationRecord

        conversation = session.get(ConversationRecord, conversation_id)
        assert conversation.title is None


def test_title_exists_for_owner_true_for_case_insensitive_match():
    owner_id = uuid.uuid4()
    conv_a, conv_b = uuid.uuid4(), uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        from app.auth.models import UserRecord

        session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.flush()
        get_or_create_conversation(session, conv_a, owner_id)
        get_or_create_conversation(session, conv_b, owner_id)
        rename_conversation(session, conv_a, owner_id, "My Chat")
        session.commit()

    with session_factory() as session:
        assert title_exists_for_owner(session, owner_id, "my chat", exclude_conversation_id=conv_b) is True


def test_title_exists_for_owner_excludes_the_conversation_itself():
    owner_id = uuid.uuid4()
    conv_a = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        from app.auth.models import UserRecord

        session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
        session.flush()
        get_or_create_conversation(session, conv_a, owner_id)
        rename_conversation(session, conv_a, owner_id, "My Chat")
        session.commit()

    with session_factory() as session:
        assert title_exists_for_owner(session, owner_id, "My Chat", exclude_conversation_id=conv_a) is False


def test_title_exists_for_owner_scoped_per_owner():
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    conv_a = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        from app.auth.models import UserRecord

        session.add(UserRecord(id=owner_a, email=f"{owner_a}@test", hashed_password="x"))
        session.add(UserRecord(id=owner_b, email=f"{owner_b}@test", hashed_password="x"))
        session.flush()
        get_or_create_conversation(session, conv_a, owner_a)
        rename_conversation(session, conv_a, owner_a, "My Chat")
        session.commit()

    with session_factory() as session:
        exists = title_exists_for_owner(
            session, owner_b, "My Chat", exclude_conversation_id=uuid.uuid4()
        )
        assert exists is False


def _make_message(owner_id):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        from app.auth.models import UserRecord

        if session.get(UserRecord, owner_id) is None:
            session.add(UserRecord(id=owner_id, email=f"{owner_id}@test", hashed_password="x"))
            session.flush()
        get_or_create_conversation(session, conversation_id, owner_id)
        message = append_message(session, conversation_id, "assistant", "an answer")
        session.commit()
        return message.id


def test_set_message_feedback_creates_a_rating():
    owner_id = uuid.uuid4()
    message_id = _make_message(owner_id)
    session_factory = get_session_factory()

    with session_factory() as session:
        assert set_message_feedback(session, message_id, owner_id, "up") is True
        session.commit()

    with session_factory() as session:
        assert get_feedback_for_messages(session, [message_id]) == {message_id: "up"}


def test_set_message_feedback_switches_an_existing_rating():
    owner_id = uuid.uuid4()
    message_id = _make_message(owner_id)
    session_factory = get_session_factory()

    with session_factory() as session:
        set_message_feedback(session, message_id, owner_id, "up")
        session.commit()
    with session_factory() as session:
        set_message_feedback(session, message_id, owner_id, "down")
        session.commit()

    with session_factory() as session:
        assert get_feedback_for_messages(session, [message_id]) == {message_id: "down"}


def test_set_message_feedback_returns_false_for_unknown_message():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert set_message_feedback(session, uuid.uuid4(), uuid.uuid4(), "up") is False


def test_set_message_feedback_returns_false_for_wrong_owner():
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    message_id = _make_message(owner_id)
    session_factory = get_session_factory()

    with session_factory() as session:
        assert set_message_feedback(session, message_id, other_owner_id, "up") is False

    with session_factory() as session:
        assert get_feedback_for_messages(session, [message_id]) == {}


def test_clear_message_feedback_removes_an_existing_rating():
    owner_id = uuid.uuid4()
    message_id = _make_message(owner_id)
    session_factory = get_session_factory()

    with session_factory() as session:
        set_message_feedback(session, message_id, owner_id, "up")
        session.commit()

    with session_factory() as session:
        assert clear_message_feedback(session, message_id, owner_id) is True
        session.commit()

    with session_factory() as session:
        assert get_feedback_for_messages(session, [message_id]) == {}


def test_clear_message_feedback_is_a_noop_when_nothing_to_clear():
    owner_id = uuid.uuid4()
    message_id = _make_message(owner_id)
    session_factory = get_session_factory()

    with session_factory() as session:
        assert clear_message_feedback(session, message_id, owner_id) is True


def test_clear_message_feedback_returns_false_for_wrong_owner():
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    message_id = _make_message(owner_id)
    session_factory = get_session_factory()

    with session_factory() as session:
        assert clear_message_feedback(session, message_id, other_owner_id) is False


def test_get_feedback_for_messages_empty_input_returns_empty_dict():
    session_factory = get_session_factory()
    with session_factory() as session:
        assert get_feedback_for_messages(session, []) == {}
