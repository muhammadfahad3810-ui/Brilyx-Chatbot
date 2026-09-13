from types import SimpleNamespace

from backend.app.events.models import EventType
from backend.app.events.rules import build_event_payload, evaluate_events


def _conversation(conv_id="conv-1"):
    return SimpleNamespace(id=conv_id)


def _state(**overrides):
    defaults = dict(demo_requested=False, human_handoff_requested=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _qualification(score=0, level="low"):
    return SimpleNamespace(score=score, level=level)


def _lead(**overrides):
    defaults = dict(
        id="lead-1",
        name=None,
        business_name=None,
        business_type="unknown",
        country=None,
        requested_service="unknown",
        estimated_package=None,
        problem_summary=None,
        requirements=[],
        email=None,
        whatsapp=None,
        website=None,
        lead_status="new",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _event_types(events):
    return {e.event_type for e in events}


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_no_events_for_low_intent_conversation():
    events = evaluate_events(_conversation(), _state(), _qualification(score=0, level="low"), None)
    assert events == []


def test_missing_state_attributes_do_not_crash():
    bare_state = SimpleNamespace()
    events = evaluate_events(_conversation(), bare_state, _qualification(), None)
    assert events == []


# ---------------------------------------------------------------------------
# Lead created / high-value
# ---------------------------------------------------------------------------


def test_lead_present_generates_lead_created_only_when_score_not_high():
    events = evaluate_events(_conversation(), _state(), _qualification(score=15, level="low"), _lead())
    assert _event_types(events) == {EventType.LEAD_CREATED.value}


def test_high_score_generates_both_lead_created_and_high_value_lead():
    events = evaluate_events(_conversation(), _state(), _qualification(score=80, level="high"), _lead())
    assert _event_types(events) == {EventType.LEAD_CREATED.value, EventType.HIGH_VALUE_LEAD.value}


def test_dedupe_keys_are_scoped_to_lead_id():
    lead = _lead(id="lead-42")
    events = evaluate_events(_conversation(), _state(), _qualification(score=80, level="high"), lead)
    keys = {e.dedupe_key for e in events}
    assert "lead_created:lead-42" in keys
    assert "high_value_lead:lead-42" in keys


# ---------------------------------------------------------------------------
# Demo / handoff
# ---------------------------------------------------------------------------


def test_demo_requested_generates_event_even_without_a_lead():
    events = evaluate_events(_conversation("conv-9"), _state(demo_requested=True), _qualification(), None)
    assert _event_types(events) == {EventType.DEMO_REQUESTED.value}
    assert events[0].dedupe_key == "demo_requested:conv-9"
    assert events[0].lead_id is None


def test_human_handoff_generates_event_scoped_to_conversation():
    events = evaluate_events(
        _conversation("conv-7"), _state(human_handoff_requested=True), _qualification(), None
    )
    assert _event_types(events) == {EventType.HUMAN_HANDOFF_REQUESTED.value}
    assert events[0].dedupe_key == "human_handoff_requested:conv-7"


def test_lead_demo_and_handoff_can_all_apply_simultaneously():
    events = evaluate_events(
        _conversation("conv-3"),
        _state(demo_requested=True, human_handoff_requested=True),
        _qualification(score=80, level="high"),
        _lead(id="lead-3"),
    )
    assert _event_types(events) == {
        EventType.LEAD_CREATED.value,
        EventType.HIGH_VALUE_LEAD.value,
        EventType.DEMO_REQUESTED.value,
        EventType.HUMAN_HANDOFF_REQUESTED.value,
    }


def test_re_evaluating_the_same_satisfied_condition_yields_the_same_dedupe_key():
    # Pure/deterministic: calling this again for a still-true condition
    # must produce an identical dedupe_key every time — that's what lets
    # the persistence layer recognize "already handled" (Phase 8 spec
    # sections 6-8: do not re-notify on every subsequent message).
    state = _state(demo_requested=True)
    conv = _conversation("conv-5")
    qual = _qualification()
    first = evaluate_events(conv, state, qual, None)
    second = evaluate_events(conv, state, qual, None)
    assert first[0].dedupe_key == second[0].dedupe_key


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_payload_pulls_fields_from_lead_and_qualification():
    lead = _lead(
        id="lead-9",
        name="Ahmed",
        business_name="Smile Dental",
        business_type="dental_clinic",
        email="ahmed@example.com",
        website="https://example.com",
        requirements=["fast replies", "whatsapp integration"],
    )
    payload = build_event_payload(_conversation("conv-9"), lead, _qualification(score=80, level="high"))

    assert payload["conversation_id"] == "conv-9"
    assert payload["lead_id"] == "lead-9"
    assert payload["name"] == "Ahmed"
    assert payload["business_name"] == "Smile Dental"
    assert payload["email"] == "ahmed@example.com"
    assert payload["website"] == "https://example.com"
    assert payload["lead_score"] == 80
    assert payload["lead_level"] == "high"
    assert payload["requirements"] == ["fast replies", "whatsapp integration"]


def test_payload_bounds_requirements_length():
    lead = _lead(requirements=[f"req-{i}" for i in range(20)])
    payload = build_event_payload(_conversation(), lead, _qualification())
    assert len(payload["requirements"]) == 10


def test_payload_without_a_lead_has_null_lead_fields():
    payload = build_event_payload(_conversation(), None, _qualification(score=0, level="low"))
    assert payload["lead_id"] is None
    assert payload["name"] is None
    assert payload["email"] is None
    assert payload["lead_score"] == 0
