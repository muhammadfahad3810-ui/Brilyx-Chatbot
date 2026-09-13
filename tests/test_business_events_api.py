from backend.app.ai.base import AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.events.models import EventStatus, EventType
from backend.app.events.notifications import NotificationError, NotificationProvider, NotificationService
from backend.app.events.service import get_notification_service
from backend.app.main import app
from backend.app.models import BusinessEvent


class RecordingFakeOrchestrator:
    def __init__(self, chat_response_text="ok", extraction_json="{}"):
        self._chat_response_text = chat_response_text
        self._extraction_json = extraction_json

    def chat(self, message, history=None, context=None):
        return AIResponse(text=self._chat_response_text, provider="fake", model="fake")

    def extract(self, system_prompt, message):
        return AIResponse(text=self._extraction_json, provider="fake", model="fake")


class FakeProvider(NotificationProvider):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        if self.should_fail:
            raise NotificationError("simulated SMTP failure")
        self.sent.append((subject, body))


def _override_orchestrator(fake):
    app.dependency_overrides[get_orchestrator] = lambda: fake


def _override_notifications(fake_provider):
    app.dependency_overrides[get_notification_service] = lambda: NotificationService(fake_provider)


def teardown_function():
    app.dependency_overrides.pop(get_orchestrator, None)
    app.dependency_overrides.pop(get_notification_service, None)


def _send(client, conversation_id, message):
    payload = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    return response.json()["conversation_id"], response.json()


# ---------------------------------------------------------------------------
# N: no public event endpoint
# ---------------------------------------------------------------------------


def test_no_public_business_event_endpoint_exists(client):
    _override_orchestrator(RecordingFakeOrchestrator())
    _override_notifications(FakeProvider())

    for path in ("/api/events", "/api/business-events", "/api/notifications"):
        response = client.get(path)
        assert response.status_code in (404, 405)
        response = client.post(path, json={"event_type": "demo_requested"})
        assert response.status_code in (404, 405)


# ---------------------------------------------------------------------------
# Demo dedup through the real chat pipeline (spec section 29)
# ---------------------------------------------------------------------------


def test_demo_dedup_across_multiple_turns(client, db_session):
    _override_orchestrator(RecordingFakeOrchestrator())
    fake_provider = FakeProvider()
    _override_notifications(fake_provider)

    conversation_id, _ = _send(client, None, "I want a demo.")
    _send(client, conversation_id, "Here is my email: test@example.com")
    _send(client, conversation_id, "I still want the demo.")

    rows = (
        db_session.query(BusinessEvent)
        .filter_by(conversation_id=conversation_id, event_type=EventType.DEMO_REQUESTED.value)
        .all()
    )
    assert len(rows) == 1
    demo_notifications = [s for s in fake_provider.sent if "Demo" in s[0]]
    assert len(demo_notifications) == 1


# ---------------------------------------------------------------------------
# Human handoff dedup through the real chat pipeline (spec section 30)
# ---------------------------------------------------------------------------


def test_human_handoff_dedup_across_multiple_turns(client, db_session):
    _override_orchestrator(RecordingFakeOrchestrator())
    fake_provider = FakeProvider()
    _override_notifications(fake_provider)

    conversation_id, _ = _send(client, None, "I want to speak to a human.")
    _send(client, conversation_id, "Please, this is urgent.")

    rows = (
        db_session.query(BusinessEvent)
        .filter_by(conversation_id=conversation_id, event_type=EventType.HUMAN_HANDOFF_REQUESTED.value)
        .all()
    )
    assert len(rows) == 1
    handoff_notifications = [s for s in fake_provider.sent if "Handoff" in s[0]]
    assert len(handoff_notifications) == 1


# ---------------------------------------------------------------------------
# High-value lead dedup through the real chat pipeline (spec section 28)
# ---------------------------------------------------------------------------


def test_high_value_lead_notified_once_even_as_score_grows(client, db_session):
    _override_orchestrator(RecordingFakeOrchestrator())
    fake_provider = FakeProvider()
    _override_notifications(fake_provider)

    conversation_id, _ = _send(client, None, "I run a dental clinic.")
    _send(client, conversation_id, "We receive many repetitive questions via whatsapp.")
    _send(client, conversation_id, "I need a WhatsApp AI agent.")
    _send(client, conversation_id, "How much does it cost?")  # score reaches 60/high here
    _send(client, conversation_id, "I'd like a demo.")  # score climbs further to 80/high

    high_value_rows = (
        db_session.query(BusinessEvent)
        .filter_by(conversation_id=conversation_id, event_type=EventType.HIGH_VALUE_LEAD.value)
        .all()
    )
    assert len(high_value_rows) == 1
    high_value_notifications = [s for s in fake_provider.sent if "High-Value" in s[0]]
    assert len(high_value_notifications) == 1


# ---------------------------------------------------------------------------
# K: SMTP failure must never break /api/chat
# ---------------------------------------------------------------------------


def test_notification_failure_does_not_break_chat_response(client, db_session):
    _override_orchestrator(RecordingFakeOrchestrator(chat_response_text="Sure, happy to help!"))
    _override_notifications(FakeProvider(should_fail=True))

    conversation_id, body = _send(client, None, "I want to speak to a human.")

    assert body["response"] == "Sure, happy to help!"
    event = (
        db_session.query(BusinessEvent)
        .filter_by(conversation_id=conversation_id, event_type=EventType.HUMAN_HANDOFF_REQUESTED.value)
        .first()
    )
    assert event is not None
    assert event.status == EventStatus.FAILED.value
    assert event.error_message


# ---------------------------------------------------------------------------
# M: no credentials/internal fields ever appear in the visitor-facing response
# ---------------------------------------------------------------------------


def test_chat_response_never_exposes_event_or_smtp_details(client):
    _override_orchestrator(RecordingFakeOrchestrator())
    _override_notifications(FakeProvider())

    response = client.post("/api/chat", json={"message": "I'd like a demo."})
    body = response.json()

    assert set(body.keys()) == {"conversation_id", "session_id", "response", "provider", "model"}
    lowered = response.text.lower()
    for forbidden in ("smtp_password", "smtp_username", "event_id", "dedupe_key", "notification", "business_event"):
        assert forbidden not in lowered


# ---------------------------------------------------------------------------
# O-Q: Phase 5/6/4.1.1 regressions spot-checked alongside the events layer
# ---------------------------------------------------------------------------


def test_qualification_and_lead_capture_unaffected_by_events_layer(client, db_session):
    _override_orchestrator(RecordingFakeOrchestrator())
    _override_notifications(FakeProvider())

    conversation_id, _ = _send(client, None, "I run a dental clinic.")
    _, last_body = _send(client, conversation_id, "I'd like a demo.")
    _send(client, conversation_id, "My email is ahmed@example.com")

    headers = {"X-Conversation-Token": last_body["session_id"]}
    qualification = client.get(f"/api/conversations/{conversation_id}/qualification", headers=headers).json()
    lead = client.get(f"/api/conversations/{conversation_id}/lead", headers=headers).json()

    assert qualification["score"] == 35  # business (+15) + demo (+20), unchanged Phase 5 math
    assert lead["email"] == "ahmed@example.com"
    assert lead["lead_status"] == "demo_requested"
