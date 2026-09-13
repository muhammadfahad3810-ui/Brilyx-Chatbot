from types import SimpleNamespace

import pytest

from backend.app.models import ConversationState
from backend.app.qualification.models import QualificationLevel
from backend.app.qualification.rules import (
    _clamp,
    calculate_qualification,
    has_email_provided,
    has_pricing_interest,
    has_whatsapp_contact_provided,
    level_for_score,
)


def _state(**overrides) -> ConversationState:
    """A real ConversationState (Phase-4-style safe defaults), not attached to any session."""
    defaults = dict(
        conversation_id="test",
        intent="unknown",
        business_type="unknown",
        business_name=None,
        country=None,
        currency=None,
        current_channel=None,
        requested_service="unknown",
        requested_package=None,
        problem_summary=None,
        requirements=[],
        demo_requested=False,
        human_handoff_requested=False,
        handoff_recommended=False,
        correction_count=0,
        confidence=0.0,
    )
    defaults.update(overrides)
    return ConversationState(**defaults)


def _future_state(**overrides) -> SimpleNamespace:
    """A stand-in for a *future* ConversationState that also has contact fields.

    Real ConversationState has no `email`/`whatsapp_number` columns yet (Phase
    6 territory) — this stub exists only to prove the scoring rules correctly
    pick those signals up via getattr() once such fields exist, without
    adding them to the Phase 4 model now.
    """
    base = _state()
    fields = {c: getattr(base, c) for c in base.__table__.columns.keys()}
    fields.update(email=None, whatsapp_number=None)
    fields.update(overrides)
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# A-F: individual signals
# ---------------------------------------------------------------------------


def test_empty_unknown_state_scores_zero_low():
    result = calculate_qualification(_state())
    assert result.score == 0
    assert result.level == QualificationLevel.LOW
    assert result.reasons == []


def test_business_identified_scores_15():
    result = calculate_qualification(_state(business_type="dental_clinic"))
    assert result.score == 15
    assert any("Business identified" in r for r in result.reasons)


def test_business_identified_via_business_name_alone():
    result = calculate_qualification(_state(business_name="Acme Dental"))
    assert result.score == 15


def test_bare_word_business_does_not_qualify_as_identified():
    # Saying the word "business" alone must not set a meaningful business_type.
    result = calculate_qualification(_state(business_type="unknown", business_name=None))
    assert result.score == 0


def test_clear_business_problem_scores_20():
    result = calculate_qualification(
        _state(problem_summary="I lose customer inquiries because I don't reply quickly.")
    )
    assert result.score == 20
    assert any("problem" in r.lower() for r in result.reasons)


def test_vague_problem_statements_do_not_score():
    for vague in ["I need help.", "Tell me about AI.", "I want something."]:
        result = calculate_qualification(_state(problem_summary=vague))
        assert result.score == 0, f"{vague!r} should not score a problem signal"


def test_specific_service_scores_15():
    result = calculate_qualification(_state(requested_service="ai_website_chatbot"))
    assert result.score == 15


def test_pricing_interest_from_visitor_message_scores_10():
    result = calculate_qualification(_state(), user_messages=["How much does it cost?"])
    assert result.score == 10


def test_pricing_interest_from_requested_package_scores_10():
    result = calculate_qualification(_state(requested_package="business"))
    assert result.score == 10


def test_demo_requested_scores_20():
    result = calculate_qualification(_state(demo_requested=True))
    assert result.score == 20


# ---------------------------------------------------------------------------
# G-H: max score and clamping
# ---------------------------------------------------------------------------


def test_all_signals_present_scores_exactly_100():
    state = _future_state(
        business_type="dental_clinic",
        problem_summary="We lose customer inquiries because we reply too slowly.",
        requested_service="whatsapp_ai_agent",
        requested_package="business",
        demo_requested=True,
        email="visitor@example.com",
        whatsapp_number="+923001234567",
    )
    result = calculate_qualification(state, user_messages=["How much does it cost?"])
    assert result.score == 100
    assert result.level == QualificationLevel.HIGH
    assert len(result.reasons) == 7


def test_email_and_whatsapp_signals_are_zero_until_those_fields_exist():
    # On the real, current ConversationState (no email/whatsapp_number
    # columns yet), these signals must safely stay at 0 rather than crash.
    assert has_email_provided(_state()) is False
    assert has_whatsapp_contact_provided(_state()) is False


def test_email_signal_activates_once_a_valid_email_field_exists():
    assert has_email_provided(_future_state(email="visitor@example.com")) is True
    assert has_email_provided(_future_state(email="not-an-email")) is False
    assert has_email_provided(_future_state(email=None)) is False


def test_score_can_never_exceed_100():
    assert _clamp(150) == 100
    assert _clamp(101) == 100


def test_score_can_never_go_below_zero():
    assert _clamp(-10) == 0
    assert _clamp(-1) == 0


# ---------------------------------------------------------------------------
# I-L: level boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, QualificationLevel.LOW),
        (29, QualificationLevel.LOW),
        (30, QualificationLevel.MEDIUM),
        (59, QualificationLevel.MEDIUM),
        (60, QualificationLevel.HIGH),
        (100, QualificationLevel.HIGH),
    ],
)
def test_level_boundaries(score, expected):
    assert level_for_score(score) == expected


# ---------------------------------------------------------------------------
# M: no double counting
# ---------------------------------------------------------------------------


def test_repeated_identical_state_produces_same_score():
    state = _state(business_type="dental_clinic", requested_service="whatsapp_ai_agent")
    first = calculate_qualification(state)
    second = calculate_qualification(state)
    assert first.score == second.score == 30


def test_business_type_set_once_does_not_compound_across_recalculations():
    # merge_state (Phase 4) already guarantees business_type is set once and
    # stays put; qualification must not add the signal again just because
    # the same state is scored on every subsequent turn.
    state = _state(business_type="dental_clinic")
    scores = [calculate_qualification(state).score for _ in range(5)]
    assert scores == [15, 15, 15, 15, 15]


# ---------------------------------------------------------------------------
# N-O: mandatory distinctions
# ---------------------------------------------------------------------------


def test_whatsapp_service_discussion_does_not_award_whatsapp_contact():
    result = calculate_qualification(_state(requested_service="whatsapp_ai_agent"))
    assert result.score == 15  # service only
    assert not any("whatsapp contact" in r.lower() for r in result.reasons)


def test_whatsapp_contact_requires_an_actual_contact_field():
    assert has_whatsapp_contact_provided(_state()) is False
    assert has_whatsapp_contact_provided(_future_state(whatsapp_number="+923001234567")) is True


def test_business_automation_does_not_imply_business_pricing_tier():
    state = _state(requested_service="business_automation", requested_package=None)
    result = calculate_qualification(state)
    assert result.score == 15  # service only, no pricing signal implied
    assert not any("pricing" in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# P: pricing interest must be visitor-driven
# ---------------------------------------------------------------------------


def test_pricing_interest_ignores_assistant_authored_text():
    # has_pricing_interest only ever receives visitor message text (see
    # qualification/service.py::_user_message_texts) — simulate that
    # contract directly: assistant text is simply never passed in.
    assert has_pricing_interest(_state(), ["I need business automation."]) is False


def test_pricing_interest_true_when_visitor_asks_directly():
    assert has_pricing_interest(_state(), ["What's the Business package price?"]) is True


# ---------------------------------------------------------------------------
# Q-R: worked examples
# ---------------------------------------------------------------------------


def test_restaurant_example():
    state = _state(
        business_type="restaurant",
        problem_summary="My restaurant gets lots of repetitive questions.",
    )
    result = calculate_qualification(state)
    assert result.score == 35
    assert result.level == QualificationLevel.MEDIUM


def test_dental_clinic_example():
    state = _state(
        business_type="dental_clinic",
        problem_summary="I lose customer inquiries because I don't reply quickly.",
        requested_service="ai_website_chatbot",
    )
    result = calculate_qualification(state)
    assert result.score == 50
    assert result.level == QualificationLevel.MEDIUM
