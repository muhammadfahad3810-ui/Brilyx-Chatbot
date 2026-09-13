from backend.app.ai.base import AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.main import app
from backend.app.models import Lead


class RecordingFakeOrchestrator:
    def __init__(self, chat_response_text="ok", extraction_json="{}"):
        self._chat_response_text = chat_response_text
        self._extraction_json = extraction_json
        self.chat_calls = []

    def chat(self, message, history=None, context=None):
        self.chat_calls.append({"message": message, "context": context})
        return AIResponse(text=self._chat_response_text, provider="fake", model="fake")

    def extract(self, system_prompt, message):
        return AIResponse(text=self._extraction_json, provider="fake", model="fake")


def _override(fake):
    app.dependency_overrides[get_orchestrator] = lambda: fake


def teardown_function():
    app.dependency_overrides.pop(get_orchestrator, None)


# conversation_id -> session_id (the access token — see docs/security.md
# "Conversation Access Control"), captured automatically by _send() so
# individual test bodies never need to juggle it themselves.
_TOKENS: dict[str, str] = {}


def _send(client, conversation_id, message):
    payload = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    body = response.json()
    _TOKENS[body["conversation_id"]] = body["session_id"]
    return body["conversation_id"], body["response"]


def _lead(client, conversation_id):
    headers = {"X-Conversation-Token": _TOKENS.get(conversation_id, "")}
    return client.get(f"/api/conversations/{conversation_id}/lead", headers=headers)


# ---------------------------------------------------------------------------
# A-B: no lead for low-intent messages
# ---------------------------------------------------------------------------


def test_no_lead_for_simple_greeting(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "Hi")

    assert _lead(client, conversation_id).status_code == 404


def test_no_lead_for_generic_services_question(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "What services do you offer?")

    assert _lead(client, conversation_id).status_code == 404


# ---------------------------------------------------------------------------
# C-F: creation triggers
# ---------------------------------------------------------------------------


def test_email_capture_creates_a_lead(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "My email is test@example.com")

    body = _lead(client, conversation_id).json()
    assert body["email"] == "test@example.com"
    assert body["lead_status"] == "new"


def test_whatsapp_capture_creates_a_lead(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "My WhatsApp is +92 300 1234567")

    body = _lead(client, conversation_id).json()
    assert body["whatsapp"] == "+923001234567"


def test_demo_request_creates_lead_with_demo_requested_status(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I'd like a demo.")

    body = _lead(client, conversation_id).json()
    assert body["lead_status"] == "demo_requested"
    assert "Visitor requested a demo." in body["notes"]


def test_human_handoff_request_creates_lead_with_qualified_status(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "Can I talk to a human?")

    body = _lead(client, conversation_id).json()
    assert body["lead_status"] == "qualified"
    assert "Human handoff requested." in body["notes"]


# ---------------------------------------------------------------------------
# G-I: contact/business fields
# ---------------------------------------------------------------------------


def test_name_capture(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I'm Ahmed. My email is ahmed@example.com")

    body = _lead(client, conversation_id).json()
    assert body["name"] == "Ahmed"


def test_business_name_capture_via_ai_extraction(client):
    # Deterministic rules alone don't extract business_name (see Phase 4) —
    # this uses the same low-confidence-triggers-AI-extraction path as
    # tests/test_chat_intelligence.py.
    fake = RecordingFakeOrchestrator(extraction_json='{"business_name": "Smile Care Dental", "confidence": 0.6}')
    _override(fake)

    conversation_id, _ = _send(client, None, "We run a small place downtown.")
    _send(client, conversation_id, "I'd like a demo.")  # trigger lead creation

    body = _lead(client, conversation_id).json()
    assert body["business_name"] == "Smile Care Dental"


def test_website_capture(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I'd like a demo.")
    _send(client, conversation_id, "Our website is https://example.com.")

    body = _lead(client, conversation_id).json()
    assert body["website"] == "https://example.com"


# ---------------------------------------------------------------------------
# J-K: multi-turn accumulation, dedup
# ---------------------------------------------------------------------------


def test_multiple_fields_captured_across_turns_in_any_order(client, db_session):
    _override(RecordingFakeOrchestrator())

    conversation_id, _ = _send(client, None, "My name is Ahmed.")
    assert _lead(client, conversation_id).status_code == 404  # name alone doesn't trigger creation

    _send(client, conversation_id, "My email is ahmed@example.com.")
    _send(client, conversation_id, "Our website is https://example.com.")

    body = _lead(client, conversation_id).json()
    assert body["name"] == "Ahmed"
    assert body["email"] == "ahmed@example.com"
    assert body["website"] == "https://example.com"

    count = db_session.query(Lead).filter_by(conversation_id=conversation_id).count()
    assert count == 1


def test_lead_is_updated_not_duplicated_across_many_turns(client, db_session):
    _override(RecordingFakeOrchestrator())

    conversation_id, _ = _send(client, None, "My email is test@example.com")
    for message in ["Our website is https://example.com.", "I'd like a demo.", "My name is Bilal."]:
        _send(client, conversation_id, message)

    count = db_session.query(Lead).filter_by(conversation_id=conversation_id).count()
    assert count == 1

    body = _lead(client, conversation_id).json()
    assert body["email"] == "test@example.com"
    assert body["website"] == "https://example.com"
    assert body["lead_status"] == "demo_requested"
    assert body["name"] == "Bilal"


# ---------------------------------------------------------------------------
# L-N: corrections
# ---------------------------------------------------------------------------


def test_email_correction_updates_the_lead(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "My email is a@example.com.")
    _send(client, conversation_id, "Actually, use b@example.com.")

    body = _lead(client, conversation_id).json()
    assert body["email"] == "b@example.com"


def test_email_is_not_overwritten_without_an_explicit_correction(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "My email is a@example.com.")
    _send(client, conversation_id, "By the way my email is c@example.com.")

    body = _lead(client, conversation_id).json()
    assert body["email"] == "a@example.com"  # no "actually/i meant" correction signal


def test_whatsapp_correction_updates_the_lead(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "My WhatsApp is 03001234567.")
    _send(client, conversation_id, "Actually my WhatsApp is 03007654321.")

    body = _lead(client, conversation_id).json()
    assert body["whatsapp"] == "03007654321"


def test_business_name_correction_flows_through_from_conversation_state(client):
    fake = RecordingFakeOrchestrator(extraction_json='{"business_name": "Old Name", "confidence": 0.6}')
    _override(fake)
    conversation_id, _ = _send(client, None, "We run a small place downtown.")
    _send(client, conversation_id, "I'd like a demo.")
    assert _lead(client, conversation_id).json()["business_name"] == "Old Name"

    fake._extraction_json = '{"business_name": "New Name", "confidence": 0.6}'
    _send(client, conversation_id, "Actually, our business is called something else downtown.")

    assert _lead(client, conversation_id).json()["business_name"] == "New Name"


# ---------------------------------------------------------------------------
# O-P: mandatory distinctions
# ---------------------------------------------------------------------------


def test_business_automation_does_not_imply_business_package(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I need business automation for my clinic.")
    _send(client, conversation_id, "I'd like a demo.")

    body = _lead(client, conversation_id).json()
    assert body["requested_service"] == "business_automation"
    assert body["estimated_package"] is None


def test_whatsapp_ai_discussion_does_not_create_whatsapp_contact(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I need a WhatsApp AI agent.")
    _send(client, conversation_id, "I'd like a demo.")  # trigger lead creation

    body = _lead(client, conversation_id).json()
    assert body["requested_service"] == "whatsapp_ai_agent"
    assert body["whatsapp"] is None


# ---------------------------------------------------------------------------
# Q-R: only visitor messages are ever scanned
# ---------------------------------------------------------------------------


def test_assistant_generated_example_email_is_not_captured(client):
    _override(RecordingFakeOrchestrator(chat_response_text="You can reach our team at sales@example.com."))
    conversation_id, _ = _send(client, None, "I'd like a demo.")

    body = _lead(client, conversation_id).json()
    assert body["email"] is None


def test_assistant_generated_phone_number_is_not_captured(client):
    _override(RecordingFakeOrchestrator(chat_response_text="Our WhatsApp support line is +92 300 9999999."))
    conversation_id, _ = _send(client, None, "I'd like a demo.")

    body = _lead(client, conversation_id).json()
    assert body["whatsapp"] is None


# ---------------------------------------------------------------------------
# S-T: deterministic score, no client manipulation
# ---------------------------------------------------------------------------


def test_lead_score_matches_phase5_qualification_endpoint(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I run a dental clinic.")
    _send(client, conversation_id, "I'd like a demo.")

    headers = {"X-Conversation-Token": _TOKENS[conversation_id]}
    lead_body = _lead(client, conversation_id).json()
    qual_body = client.get(f"/api/conversations/{conversation_id}/qualification", headers=headers).json()

    assert lead_body["lead_score"] == qual_body["score"]
    assert lead_body["lead_level"] == qual_body["level"]


def test_client_supplied_score_and_status_in_chat_request_are_ignored(client):
    _override(RecordingFakeOrchestrator())
    response = client.post(
        "/api/chat",
        json={
            "message": "My email is test@example.com",
            "lead_score": 999,
            "lead_status": "converted",
            "lead_level": "high",
        },
    )
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]
    _TOKENS[conversation_id] = response.json()["session_id"]

    body = _lead(client, conversation_id).json()
    assert body["lead_score"] != 999
    assert body["lead_status"] != "converted"


def test_no_public_lead_creation_endpoint_exists(client):
    response = client.post("/api/leads", json={"email": "test@example.com", "lead_score": 100})
    assert response.status_code in (404, 405)


# ---------------------------------------------------------------------------
# U-W: status thresholds
# ---------------------------------------------------------------------------


def test_low_score_lead_status_is_new(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "My email is test@example.com")

    body = _lead(client, conversation_id).json()
    assert body["lead_level"] == "low"
    assert body["lead_status"] == "new"


def test_high_score_lead_status_is_qualified(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I run a dental clinic.")
    _send(client, conversation_id, "We receive many repetitive questions via whatsapp.")
    _send(client, conversation_id, "I need a WhatsApp AI agent.")
    _send(client, conversation_id, "How much does it cost?")

    body = _lead(client, conversation_id).json()
    assert body["lead_score"] >= 60
    assert body["lead_level"] == "high"
    assert body["lead_status"] == "qualified"


def test_demo_requested_status(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I'd like a demo.")

    body = _lead(client, conversation_id).json()
    assert body["lead_status"] == "demo_requested"


# ---------------------------------------------------------------------------
# X-Z: no regressions
# ---------------------------------------------------------------------------


def test_existing_conversation_state_and_qualification_remain_intact(client):
    _override(RecordingFakeOrchestrator())
    conversation_id, _ = _send(client, None, "I run a dental clinic.")
    _send(client, conversation_id, "I'd like a demo.")

    headers = {"X-Conversation-Token": _TOKENS[conversation_id]}
    state = client.get(f"/api/conversations/{conversation_id}/state", headers=headers).json()
    qualification = client.get(f"/api/conversations/{conversation_id}/qualification", headers=headers).json()

    assert state["business_type"] == "dental_clinic"
    assert state["demo_requested"] is True
    assert qualification["score"] > 0


# ---------------------------------------------------------------------------
# AA-AB: debug endpoint + defensive behavior
# ---------------------------------------------------------------------------


def test_lead_endpoint_for_unknown_conversation_returns_404(client):
    assert _lead(client, "does-not-exist").status_code == 404


def test_lead_endpoint_never_exposes_internal_fields_to_chat_response(client):
    _override(RecordingFakeOrchestrator())
    response = client.post("/api/chat", json={"message": "I'd like a demo."})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"conversation_id", "session_id", "response", "provider", "model"}
    assert "lead_score" not in response.text
    assert "lead_status" not in response.text
