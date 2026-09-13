from types import SimpleNamespace

from backend.app.leads.models import LeadStatus
from backend.app.leads.rules import (
    LEAD_QUALIFIED_SCORE_THRESHOLD,
    compute_lead_status,
    compute_notes,
    extract_contact_fields,
    should_create_lead,
)


def _state(**overrides) -> SimpleNamespace:
    defaults = dict(demo_requested=False, human_handoff_requested=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Deterministic contact extraction
# ---------------------------------------------------------------------------


def test_email_extraction_from_natural_phrasing():
    for message, expected in [
        ("My email is test@example.com", "test@example.com"),
        ("Contact me at sales@company.pk", "sales@company.pk"),
        ("you can email me at hello@business.co.uk", "hello@business.co.uk"),
    ]:
        assert extract_contact_fields(message).email == expected


def test_email_extraction_returns_none_when_absent():
    assert extract_contact_fields("How much does it cost?").email is None


def test_whatsapp_ai_discussion_does_not_capture_a_contact_number():
    fields = extract_contact_fields("I want a WhatsApp AI agent.")
    assert fields.whatsapp is None
    assert fields.email is None


def test_whatsapp_contact_capture_requires_explicit_whatsapp_mention():
    fields = extract_contact_fields("My WhatsApp is +92 300 1234567")
    assert fields.whatsapp == "+923001234567"


def test_bare_phone_number_without_whatsapp_mention_is_not_captured():
    fields = extract_contact_fields("You can reach me at 03001234567")
    assert fields.whatsapp is None


def test_whatsapp_normalization_preserves_given_country_code_only():
    fields = extract_contact_fields("whatsapp me at 0300-1234567")
    assert fields.whatsapp == "03001234567"  # no country code invented


def test_website_extraction_supports_common_forms():
    assert extract_contact_fields("Our website is https://example.com.").website == "https://example.com"
    assert extract_contact_fields("check www.example.com").website == "https://www.example.com"
    assert extract_contact_fields("our site is example.com").website == "https://example.com"


def test_website_extraction_ignores_email_domain():
    fields = extract_contact_fields("My email is test@example.com")
    assert fields.website is None
    assert fields.email == "test@example.com"


def test_name_extraction_supported_phrasings():
    assert extract_contact_fields("I'm Ahmed.").name == "Ahmed"
    assert extract_contact_fields("My name is Ahmed Khan.").name == "Ahmed Khan"
    assert extract_contact_fields("You can call me Sarah.").name == "Sarah"


def test_name_extraction_is_conservative_about_ambiguous_phrasing():
    assert extract_contact_fields("I'm looking for a chatbot solution.").name is None
    assert extract_contact_fields("I'm interested in your services.").name is None


def test_multi_word_name_capture_stops_at_a_connector_word():
    # Regression: "my name is X and my email is ..." must not swallow
    # "and my" into the captured name.
    fields = extract_contact_fields("My name is Ahmed and my email is ahmed@example.com.")
    assert fields.name == "Ahmed"
    assert fields.email == "ahmed@example.com"


# ---------------------------------------------------------------------------
# Lead creation trigger (section 22)
# ---------------------------------------------------------------------------


def test_no_trigger_for_simple_greeting():
    fields = extract_contact_fields("Hi")
    assert should_create_lead(_state(), fields, score=0) is False


def test_no_trigger_for_generic_services_question():
    fields = extract_contact_fields("What services do you offer?")
    assert should_create_lead(_state(), fields, score=0) is False


def test_email_triggers_lead_creation():
    fields = extract_contact_fields("My email is test@example.com")
    assert should_create_lead(_state(), fields, score=0) is True


def test_whatsapp_contact_triggers_lead_creation():
    fields = extract_contact_fields("My WhatsApp is +92 300 1234567")
    assert should_create_lead(_state(), fields, score=0) is True


def test_demo_requested_triggers_lead_creation():
    fields = extract_contact_fields("I'd like a demo.")
    assert should_create_lead(_state(demo_requested=True), fields, score=20) is True


def test_human_handoff_triggers_lead_creation():
    fields = extract_contact_fields("Can I talk to a human?")
    assert should_create_lead(_state(human_handoff_requested=True), fields, score=0) is True


def test_high_qualification_score_alone_triggers_lead_creation():
    fields = extract_contact_fields("How much does it cost?")
    assert should_create_lead(_state(), fields, score=LEAD_QUALIFIED_SCORE_THRESHOLD) is True
    assert should_create_lead(_state(), fields, score=LEAD_QUALIFIED_SCORE_THRESHOLD - 1) is False


# ---------------------------------------------------------------------------
# Lead status rules (section 28)
# ---------------------------------------------------------------------------


def test_low_score_status_is_new():
    assert compute_lead_status(None, _state(), score=15) == LeadStatus.NEW.value


def test_high_score_status_is_qualified():
    assert compute_lead_status(None, _state(), score=60) == LeadStatus.QUALIFIED.value


def test_demo_requested_status_overrides_score():
    assert compute_lead_status(None, _state(demo_requested=True), score=15) == LeadStatus.DEMO_REQUESTED.value


def test_human_handoff_status_is_qualified():
    assert compute_lead_status(None, _state(human_handoff_requested=True), score=0) == LeadStatus.QUALIFIED.value


def test_do_not_contact_status_is_never_overridden():
    status = compute_lead_status(LeadStatus.DO_NOT_CONTACT.value, _state(demo_requested=True), score=100)
    assert status == LeadStatus.DO_NOT_CONTACT.value


def test_never_sets_contacted_demo_sent_converted_or_lost():
    possible_states = [
        _state(),
        _state(demo_requested=True),
        _state(human_handoff_requested=True),
    ]
    forbidden = {
        LeadStatus.CONTACTED.value,
        LeadStatus.DEMO_SENT.value,
        LeadStatus.CONVERTED.value,
        LeadStatus.LOST.value,
    }
    for state in possible_states:
        for score in (0, 30, 60, 100):
            assert compute_lead_status(None, state, score) not in forbidden


# ---------------------------------------------------------------------------
# Notes (section 21)
# ---------------------------------------------------------------------------


def test_notes_are_deterministic_and_system_generated():
    assert compute_notes(_state()) == ["Captured from Brilyx website chatbot."]
    assert compute_notes(_state(demo_requested=True)) == [
        "Captured from Brilyx website chatbot.",
        "Visitor requested a demo.",
    ]
    assert compute_notes(_state(human_handoff_requested=True)) == [
        "Captured from Brilyx website chatbot.",
        "Human handoff requested.",
    ]


# ---------------------------------------------------------------------------
# Defensive handling of missing/unknown fields (AB)
# ---------------------------------------------------------------------------


def test_missing_state_fields_do_not_crash():
    bare_state = SimpleNamespace()  # no demo_requested / human_handoff_requested at all
    fields = extract_contact_fields("Hi")
    assert should_create_lead(bare_state, fields, score=0) is False
    assert compute_lead_status(None, bare_state, score=0) == LeadStatus.NEW.value
    assert compute_notes(bare_state) == ["Captured from Brilyx website chatbot."]
