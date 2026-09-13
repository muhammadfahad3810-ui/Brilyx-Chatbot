from backend.app.intelligence.models import BusinessType, Channel, ExtractedFields, Intent, PackageTier, RequestedService
from backend.app.intelligence.service import build_context_lines, merge_state
from backend.app.models import ConversationState


def _fresh_state() -> ConversationState:
    return ConversationState(
        conversation_id="conv-1",
        intent="unknown",
        business_type="unknown",
        requested_service="unknown",
        requirements=[],
        demo_requested=False,
        human_handoff_requested=False,
        handoff_recommended=False,
        correction_count=0,
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# STATE MERGING
# ---------------------------------------------------------------------------


def test_fill_empty_field():
    state = _fresh_state()
    fields = ExtractedFields(business_type=BusinessType.DENTAL_CLINIC, confidence=0.7)

    merge_state(state, fields, "I run a dental clinic.")

    assert state.business_type == "dental_clinic"


def test_retain_existing_field_when_not_mentioned_again():
    state = _fresh_state()
    merge_state(state, ExtractedFields(business_type=BusinessType.DENTAL_CLINIC, confidence=0.7), "I run a dental clinic.")

    merge_state(
        state,
        ExtractedFields(current_channel=None, problem_summary="High volume of WhatsApp inquiries", confidence=0.75),
        "We get lots of questions on WhatsApp.",
    )

    assert state.business_type == "dental_clinic"
    assert state.problem_summary == "High volume of WhatsApp inquiries"


def test_explicit_correction_replaces_sticky_value():
    state = _fresh_state()
    merge_state(state, ExtractedFields(business_type=BusinessType.RESTAURANT, confidence=0.7), "I run a restaurant.")
    assert state.business_type == "restaurant"

    merge_state(state, ExtractedFields(business_type=BusinessType.CAFE, confidence=0.7), "Actually, it's a cafe.")

    assert state.business_type == "cafe"


def test_non_correction_message_does_not_change_sticky_value_when_not_rementioned():
    state = _fresh_state()
    merge_state(state, ExtractedFields(business_type=BusinessType.RESTAURANT, confidence=0.7), "I run a restaurant.")

    # "We also sell desserts" doesn't re-detect a business_type at all, so
    # this call simply carries no business_type signal (fields.business_type
    # is None) — the sticky value must remain untouched either way.
    merge_state(state, ExtractedFields(confidence=0.0), "We also sell desserts.")

    assert state.business_type == "restaurant"


def test_null_or_unknown_never_overwrites_a_known_value():
    state = _fresh_state()
    merge_state(state, ExtractedFields(business_type=BusinessType.DENTAL_CLINIC, confidence=0.7), "I run a dental clinic.")

    merge_state(state, ExtractedFields(business_type=None, confidence=0.0), "How much does it cost?")
    assert state.business_type == "dental_clinic"

    merge_state(state, ExtractedFields(business_type=BusinessType.UNKNOWN, confidence=0.3), "Something unrelated.")
    assert state.business_type == "dental_clinic"


def test_topic_fields_freely_update_without_correction_phrasing():
    state = _fresh_state()
    merge_state(state, ExtractedFields(requested_package=PackageTier.STARTER, confidence=0.9), "What about Starter?")
    assert state.requested_package == "starter"

    merge_state(state, ExtractedFields(requested_package=PackageTier.PRO, confidence=0.9), "And the Pro package?")
    assert state.requested_package == "pro"


def test_requirements_are_additive_and_deduplicated():
    state = _fresh_state()
    merge_state(state, ExtractedFields(requirements=["FAQ answering"], confidence=0.5), "It should answer FAQs.")
    merge_state(state, ExtractedFields(requirements=["FAQ answering", "Lead collection"], confidence=0.5), "And collect leads.")

    assert state.requirements == ["FAQ answering", "Lead collection"]


def test_full_dental_clinic_conversation_sequence_matches_spec_example():
    state = _fresh_state()

    merge_state(state, ExtractedFields(business_type=BusinessType.DENTAL_CLINIC, confidence=0.7), "I run a dental clinic.")
    assert state.business_type == "dental_clinic"

    merge_state(
        state,
        ExtractedFields(current_channel=Channel.WHATSAPP, problem_summary="High volume of WhatsApp inquiries", confidence=0.75),
        "We get lots of WhatsApp inquiries.",
    )
    assert state.business_type == "dental_clinic"
    assert state.current_channel == "whatsapp"
    assert state.problem_summary

    merge_state(state, ExtractedFields(intent=Intent.PRICING, confidence=0.85), "How much does it cost?")
    assert state.business_type == "dental_clinic"
    assert state.current_channel == "whatsapp"
    assert state.problem_summary
    assert state.intent == "pricing"


# ---------------------------------------------------------------------------
# DEMO / HUMAN HANDOFF persistence (monotonic booleans)
# ---------------------------------------------------------------------------


def test_demo_requested_persists_true_once_set():
    state = _fresh_state()
    merge_state(state, ExtractedFields(demo_requested=True, confidence=0.9), "Can I see a demo?")
    assert state.demo_requested is True

    merge_state(state, ExtractedFields(confidence=0.0), "Unrelated follow-up.")
    assert state.demo_requested is True


def test_human_handoff_requested_persists_true_once_set():
    state = _fresh_state()
    merge_state(state, ExtractedFields(human_handoff_requested=True, confidence=0.95), "Can I talk to a human?")
    assert state.human_handoff_requested is True

    merge_state(state, ExtractedFields(confidence=0.0), "Unrelated follow-up.")
    assert state.human_handoff_requested is True


def test_handoff_recommended_for_custom_package_request():
    state = _fresh_state()
    merge_state(state, ExtractedFields(requested_package=PackageTier.CUSTOM, confidence=0.9), "What about a custom package?")
    assert state.handoff_recommended is True
    assert state.human_handoff_requested is False  # explicit vs recommended stay distinct


def test_handoff_recommended_for_many_requirements():
    state = _fresh_state()
    merge_state(
        state,
        ExtractedFields(requirements=["a", "b", "c"], confidence=0.5),
        "I need a, b, and c.",
    )
    assert state.handoff_recommended is True


# ---------------------------------------------------------------------------
# Context line rendering never exposes internal-only bookkeeping
# ---------------------------------------------------------------------------


def test_context_lines_empty_for_fresh_state():
    assert build_context_lines(_fresh_state()) == []


def test_context_lines_include_known_fields_only():
    state = _fresh_state()
    merge_state(state, ExtractedFields(business_type=BusinessType.DENTAL_CLINIC, confidence=0.7), "I run a dental clinic.")
    merge_state(state, ExtractedFields(requested_service=RequestedService.WHATSAPP_AI_AGENT, confidence=0.85), "I want WhatsApp AI.")

    lines = build_context_lines(state)

    assert any("Dental Clinic" in line for line in lines)
    assert any("Whatsapp Ai Agent" in line or "Whatsapp" in line for line in lines)
