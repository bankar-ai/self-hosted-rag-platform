"""Cheap, deterministic output-side check for prompt leakage / injection compliance (ERP-109).

Defense-in-depth alongside ERP-058's input-side mitigation (retrieved content wrapped in
`<untrusted_context>` tags, an explicit system-prompt instruction never to follow embedded
commands) -- ERP-058 is documented as a mitigation, not a guarantee, and until this ticket
there was no output-side check at all: if the input-side mitigation ever failed, nothing
downstream would catch it before the (possibly compromised) answer reached the user.

Deliberately a non-LLM check: a second judge-LLM call on every answer would fight the
cold-start/latency problem ERP-097/106 just spent a session fixing, and true real-time
streaming interception isn't reconcilable with actually streaming (you can't safely evaluate a
response before it's complete without buffering the whole thing first). This runs against the
fully-assembled answer text, after generation completes, in both the sync `generate()` and the
end of `generate_stream()` -- zero added latency, since persistence already happens at that
same point.

Detection/logging only in this first pass: a match is logged (never blocks or alters the
response) given the false-positive risk of a purely heuristic pattern check on natural
language -- a human should be able to review what got flagged rather than a legitimate answer
just vanishing. Whether a confirmed match should ever block/redact automatically is a separate,
larger decision left for a future ticket.
"""

import logging
import re
import uuid

from app.generation.prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# A verbatim substring of SYSTEM_PROMPT long enough that only genuine leakage of the prompt
# text itself -- not a coincidental phrase overlap -- would ever match it in a generated answer.
_SYSTEM_PROMPT_LEAK_PROBE = SYSTEM_PROMPT[:80]

# Known injection-compliance markers: phrases that show up in an answer only if the model
# followed an embedded command instead of treating <untrusted_context> as inert reference data
# (the exact ERP-058 PWNED-style attack this is meant to catch, if the input-side mitigation
# had failed to prevent it).
_COMPLIANCE_MARKERS = [
    re.compile(r"system\s*override", re.IGNORECASE),
    re.compile(r"ignore\s+(all|previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"\bPWNED\b"),
]


def check_output_guardrail(
    answer: str, conversation_id: uuid.UUID | None, message_id: uuid.UUID | None
) -> None:
    """Log (never raise or alter `answer`) if it looks like a leaked prompt or a followed injection.

    `conversation_id`/`message_id` are `None` for a stateless request (nothing persisted, so
    there's no ID to log) -- the log entry still carries the matched pattern(s) either way.
    """
    matches: list[str] = []
    if _SYSTEM_PROMPT_LEAK_PROBE in answer:
        matches.append("system_prompt_leak")
    for pattern in _COMPLIANCE_MARKERS:
        if pattern.search(answer):
            matches.append(pattern.pattern)

    if matches:
        logger.warning(
            "Output guardrail flagged a generated answer (monitoring only, not blocked)",
            extra={
                "conversation_id": str(conversation_id) if conversation_id else None,
                "message_id": str(message_id) if message_id else None,
                "guardrail_matches": matches,
            },
        )
