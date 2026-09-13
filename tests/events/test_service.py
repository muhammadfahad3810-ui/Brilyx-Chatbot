import pytest

from backend.app.config import settings
from backend.app.events.models import EventStatus, EventType
from backend.app.events.notifications import NotificationError, NotificationProvider, NotificationService
from backend.app.events.service import evaluate_and_persist_events
from backend.app.models import BusinessEvent, Conversation, ConversationState, Lead, LeadQualification


class FakeProvider(NotificationProvider):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        if self.should_fail:
            raise NotificationError("simulated SMTP failure")
        self.sent.append((subject, body))


def _make_conversation(db_session, **state_overrides) -> Conversation:
    conversation = Conversation()
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    state_defaults = dict(
        conversation_id=conversation.id,
        intent="unknown",
        business_type="dental_clinic",
        requested_service="whatsapp_ai_agent",
        requirements=[],
        demo_requested=False,
        human_handoff_requested=False,
        handoff_recommended=False,
        correction_count=0,
        confidence=0.0,
    )
    state_defaults.update(state_overrides)
    state = ConversationState(**state_defaults)
    db_session.add(state)
    db_session.commit()
    db_session.refresh(state)

    return conversation, state


def _make_qualification(db_session, conversation, score=15, level="low") -> LeadQualification:
    qualification = LeadQualification(conversation_id=conversation.id, score=score, level=level, reasons=[])
    db_session.add(qualification)
    db_session.commit()
    db_session.refresh(qualification)
    return qualification


def _make_lead(db_session, conversation, **overrides) -> Lead:
    defaults = dict(
        conversation_id=conversation.id,
        name="Ahmed",
        email="ahmed@example.com",
        business_type="dental_clinic",
        requested_service="whatsapp_ai_agent",
        lead_score=15,
        lead_level="low",
        lead_status="new",
    )
    defaults.update(overrides)
    lead = Lead(**defaults)
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


# ---------------------------------------------------------------------------
# A-B: lead_created creation + dedup
# ---------------------------------------------------------------------------


def test_lead_creation_generates_lead_created_event(db_session):
    conversation, state = _make_conversation(db_session)
    qualification = _make_qualification(db_session, conversation)
    lead = _make_lead(db_session, conversation)
    notification_service = NotificationService(FakeProvider())

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    event_types = {e.event_type for e in created}
    assert EventType.LEAD_CREATED.value in event_types
    rows = db_session.query(BusinessEvent).filter_by(event_type=EventType.LEAD_CREATED.value).all()
    assert len(rows) == 1
    assert rows[0].dedupe_key == f"lead_created:{lead.id}"
    assert rows[0].lead_id == lead.id


def test_same_lead_does_not_create_duplicate_lead_created_event(db_session):
    conversation, state = _make_conversation(db_session)
    qualification = _make_qualification(db_session, conversation)
    lead = _make_lead(db_session, conversation)
    notification_service = NotificationService(FakeProvider())

    evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)
    second = evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    assert all(e.event_type != EventType.LEAD_CREATED.value for e in second)
    rows = db_session.query(BusinessEvent).filter_by(event_type=EventType.LEAD_CREATED.value).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# C-D: high_value_lead creation + dedup
# ---------------------------------------------------------------------------


def test_high_score_generates_high_value_lead_event(db_session):
    conversation, state = _make_conversation(db_session)
    qualification = _make_qualification(db_session, conversation, score=80, level="high")
    lead = _make_lead(db_session, conversation, lead_score=80, lead_level="high", lead_status="qualified")
    notification_service = NotificationService(FakeProvider())

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    assert any(e.event_type == EventType.HIGH_VALUE_LEAD.value for e in created)


def test_high_value_event_not_recreated_on_repeated_evaluation(db_session):
    conversation, state = _make_conversation(db_session)
    qualification = _make_qualification(db_session, conversation, score=60, level="high")
    lead = _make_lead(db_session, conversation, lead_score=60, lead_level="high", lead_status="qualified")
    notification_service = NotificationService(FakeProvider())

    evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)
    # Simulate the score climbing further on a later message — must not
    # create a second identical notification (Phase 8 spec section 6).
    qualification.score = 90
    db_session.add(qualification)
    db_session.commit()
    second = evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    assert all(e.event_type != EventType.HIGH_VALUE_LEAD.value for e in second)
    rows = db_session.query(BusinessEvent).filter_by(event_type=EventType.HIGH_VALUE_LEAD.value).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# E-F: demo_requested creation + dedup
# ---------------------------------------------------------------------------


def test_demo_requested_generates_one_event(db_session):
    conversation, state = _make_conversation(db_session, demo_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(FakeProvider())

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    assert any(e.event_type == EventType.DEMO_REQUESTED.value for e in created)


def test_repeated_demo_messages_do_not_duplicate_event(db_session):
    conversation, state = _make_conversation(db_session, demo_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(FakeProvider())

    evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)
    evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)
    evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    rows = db_session.query(BusinessEvent).filter_by(event_type=EventType.DEMO_REQUESTED.value).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# G-H: human_handoff_requested creation + dedup
# ---------------------------------------------------------------------------


def test_human_handoff_generates_one_event(db_session):
    conversation, state = _make_conversation(db_session, human_handoff_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(FakeProvider())

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    assert any(e.event_type == EventType.HUMAN_HANDOFF_REQUESTED.value for e in created)


def test_repeated_handoff_requests_do_not_spam_events(db_session):
    conversation, state = _make_conversation(db_session, human_handoff_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(FakeProvider())

    for _ in range(3):
        evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    rows = db_session.query(BusinessEvent).filter_by(event_type=EventType.HUMAN_HANDOFF_REQUESTED.value).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# I: dedupe keys are unique at the database level
# ---------------------------------------------------------------------------


def test_dedupe_key_column_enforces_uniqueness_at_the_database_level(db_session):
    conversation, _ = _make_conversation(db_session)
    db_session.add(BusinessEvent(conversation_id=conversation.id, event_type="demo_requested", dedupe_key="dup-key", payload={}))
    db_session.commit()

    db_session.add(BusinessEvent(conversation_id=conversation.id, event_type="demo_requested", dedupe_key="dup-key", payload={}))
    with pytest.raises(Exception):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# J: notification receives the correct event payload
# ---------------------------------------------------------------------------


def test_notification_provider_receives_correct_payload_content(db_session):
    conversation, state = _make_conversation(db_session, demo_requested=True)
    qualification = _make_qualification(db_session, conversation, score=80, level="high")
    lead = _make_lead(db_session, conversation, name="Ahmed", email="ahmed@example.com", lead_score=80, lead_level="high")
    fake_provider = FakeProvider()
    notification_service = NotificationService(fake_provider)

    evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    assert fake_provider.sent, "expected at least one notification to have been sent"
    subject, body = fake_provider.sent[0]
    assert "Demo" in subject or "demo" in subject.lower() or "High-Value" in subject or "high" in subject.lower()
    assert "Ahmed" in body
    assert "ahmed@example.com" in body
    assert conversation.id in body


# ---------------------------------------------------------------------------
# K-L: SMTP failure does not break persistence, is recorded correctly
# ---------------------------------------------------------------------------


def test_notification_failure_is_recorded_without_raising(db_session):
    conversation, state = _make_conversation(db_session, demo_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(FakeProvider(should_fail=True))

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    assert len(created) == 1
    event = created[0]
    assert event.status == EventStatus.FAILED.value
    assert event.error_message
    assert "simulated SMTP failure" in event.error_message
    assert event.processed_at is not None


def test_event_row_remains_after_notification_failure(db_session):
    conversation, state = _make_conversation(db_session, human_handoff_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(FakeProvider(should_fail=True))

    evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    rows = db_session.query(BusinessEvent).filter_by(event_type=EventType.HUMAN_HANDOFF_REQUESTED.value).all()
    assert len(rows) == 1
    assert rows[0].status == EventStatus.FAILED.value


# ---------------------------------------------------------------------------
# Not-configured provider behaves like a graceful, recorded failure
# ---------------------------------------------------------------------------


def test_unconfigured_notification_service_records_failure_gracefully(db_session):
    conversation, state = _make_conversation(db_session, demo_requested=True)
    qualification = _make_qualification(db_session, conversation)
    notification_service = NotificationService(provider=None)

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, None, notification_service)

    assert created[0].status == EventStatus.FAILED.value
    assert "not configured" in created[0].error_message.lower()


# ---------------------------------------------------------------------------
# lead_created notifications stay off by default
# ---------------------------------------------------------------------------


def test_lead_created_is_not_notified_by_default(db_session, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFY_ON_LEAD_CREATED", False)
    conversation, state = _make_conversation(db_session)
    qualification = _make_qualification(db_session, conversation)
    lead = _make_lead(db_session, conversation)
    fake_provider = FakeProvider()
    notification_service = NotificationService(fake_provider)

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    lead_created_event = next(e for e in created if e.event_type == EventType.LEAD_CREATED.value)
    assert lead_created_event.status == EventStatus.PENDING.value
    assert fake_provider.sent == []


def test_lead_created_is_notified_when_explicitly_enabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFY_ON_LEAD_CREATED", True)
    conversation, state = _make_conversation(db_session)
    qualification = _make_qualification(db_session, conversation)
    lead = _make_lead(db_session, conversation)
    fake_provider = FakeProvider()
    notification_service = NotificationService(fake_provider)

    created = evaluate_and_persist_events(db_session, conversation, state, qualification, lead, notification_service)

    lead_created_event = next(e for e in created if e.event_type == EventType.LEAD_CREATED.value)
    assert lead_created_event.status == EventStatus.COMPLETED.value
    assert len(fake_provider.sent) == 1
