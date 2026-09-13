from backend.app.events.models import EventType, PendingEvent
from backend.app.qualification.models import QualificationLevel

MAX_PAYLOAD_REQUIREMENTS = 10


def build_event_payload(conversation, lead, qualification) -> dict:
    """A bounded, structured snapshot for a notification — never the full conversation.

    Every field here is either already-validated/bounded upstream (Lead's
    own column limits, ConversationState's truncation) or a short scalar.
    No message content, no raw AI output, no secrets. See Phase 8 spec
    section 21 and docs/business-actions.md.
    """
    payload = {
        "conversation_id": conversation.id,
        "lead_id": lead.id if lead else None,
        "name": lead.name if lead else None,
        "business_name": lead.business_name if lead else None,
        "business_type": lead.business_type if lead else None,
        "country": lead.country if lead else None,
        "requested_service": lead.requested_service if lead else None,
        "requested_package": lead.estimated_package if lead else None,
        "problem_summary": lead.problem_summary if lead else None,
        "requirements": list(lead.requirements or [])[:MAX_PAYLOAD_REQUIREMENTS] if lead else [],
        "email": lead.email if lead else None,
        "whatsapp": lead.whatsapp if lead else None,
        "website": lead.website if lead else None,
        "lead_score": qualification.score,
        "lead_level": qualification.level,
        "lead_status": lead.lead_status if lead else None,
    }
    return payload


def evaluate_events(conversation, state, qualification, lead) -> list[PendingEvent]:
    """Deterministically decide which business events apply right now.

    Pure function: no DB access, no side effects, no LLM. Idempotency is
    enforced later, at persistence time, via `dedupe_key` — this function
    is free to return the "same" event on every call for an
    already-satisfied condition (e.g. `demo_requested` stays true for the
    rest of the conversation); the service layer is what makes sure only
    the first occurrence actually creates a row / sends a notification.

    `state`/`qualification`/`lead` are read defensively via getattr where
    it matters, so a partial/stub object in tests never crashes this.
    """
    events: list[PendingEvent] = []

    if lead is not None:
        payload = build_event_payload(conversation, lead, qualification)
        events.append(
            PendingEvent(
                event_type=EventType.LEAD_CREATED.value,
                dedupe_key=f"lead_created:{lead.id}",
                payload=payload,
                lead_id=lead.id,
            )
        )
        if qualification.level == QualificationLevel.HIGH.value:
            events.append(
                PendingEvent(
                    event_type=EventType.HIGH_VALUE_LEAD.value,
                    dedupe_key=f"high_value_lead:{lead.id}",
                    payload=payload,
                    lead_id=lead.id,
                )
            )

    if getattr(state, "demo_requested", False):
        events.append(
            PendingEvent(
                event_type=EventType.DEMO_REQUESTED.value,
                dedupe_key=f"demo_requested:{conversation.id}",
                payload=build_event_payload(conversation, lead, qualification),
                lead_id=lead.id if lead else None,
            )
        )

    if getattr(state, "human_handoff_requested", False):
        events.append(
            PendingEvent(
                event_type=EventType.HUMAN_HANDOFF_REQUESTED.value,
                dedupe_key=f"human_handoff_requested:{conversation.id}",
                payload=build_event_payload(conversation, lead, qualification),
                lead_id=lead.id if lead else None,
            )
        )

    return events
