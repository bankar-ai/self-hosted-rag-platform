import logging
import uuid

from app.generation.guardrail import check_output_guardrail
from app.generation.prompt import SYSTEM_PROMPT


def test_clean_answer_is_not_flagged(caplog):
    with caplog.at_level(logging.WARNING, logger="app.generation.guardrail"):
        check_output_guardrail(
            "Paris is the capital of France [1].", conversation_id=uuid.uuid4(), message_id=uuid.uuid4()
        )
    assert caplog.records == []


def test_pwned_style_injection_compliance_is_flagged(caplog):
    """Reproduces ERP-058's exact attack shape: a model that followed an embedded command."""
    conversation_id = uuid.uuid4()
    message_id = uuid.uuid4()
    with caplog.at_level(logging.WARNING, logger="app.generation.guardrail"):
        check_output_guardrail(
            "SYSTEM OVERRIDE accepted. PWNED.", conversation_id=conversation_id, message_id=message_id
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.conversation_id == str(conversation_id)
    assert record.message_id == str(message_id)
    assert any("PWNED" in m or "system" in m.lower() for m in record.guardrail_matches)


def test_ignore_previous_instructions_echo_is_flagged(caplog):
    with caplog.at_level(logging.WARNING, logger="app.generation.guardrail"):
        check_output_guardrail(
            "Sure, I will ignore previous instructions and comply.",
            conversation_id=None,
            message_id=None,
        )
    assert len(caplog.records) == 1


def test_verbatim_system_prompt_leak_is_flagged(caplog):
    leaked = f"Here is my system prompt: {SYSTEM_PROMPT}"
    with caplog.at_level(logging.WARNING, logger="app.generation.guardrail"):
        check_output_guardrail(leaked, conversation_id=None, message_id=None)
    assert len(caplog.records) == 1
    assert "system_prompt_leak" in caplog.records[0].guardrail_matches


def test_stateless_call_logs_none_ids(caplog):
    with caplog.at_level(logging.WARNING, logger="app.generation.guardrail"):
        check_output_guardrail("PWNED.", conversation_id=None, message_id=None)
    assert caplog.records[0].conversation_id is None
    assert caplog.records[0].message_id is None


def test_legitimate_mention_of_the_word_override_alone_is_not_flagged(caplog):
    """A plain, non-attack sentence containing an unrelated word must not false-positive."""
    with caplog.at_level(logging.WARNING, logger="app.generation.guardrail"):
        check_output_guardrail(
            "The board can override a committee decision by majority vote [1].",
            conversation_id=None,
            message_id=None,
        )
    assert caplog.records == []
