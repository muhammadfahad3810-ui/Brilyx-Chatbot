import logging
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import Settings, settings
from backend.app.events.models import EventStatus, EventType
from backend.app.events.notifications import (
    NotificationError,
    NotificationProvider,
    NotificationService,
    ResendNotificationProvider,
    SMTPNotificationProvider,
)
from backend.app.events.rules import evaluate_events
from backend.app.models import BusinessEvent, Conversation, ConversationState, Lead, LeadQualification

logger = logging.getLogger("brilyx.events")

# lead_created is persisted for every lead (audit trail) but only emails the
# owner when explicitly opted in — see Settings.NOTIFY_ON_LEAD_CREATED.
ALWAYS_NOTIFIABLE_EVENT_TYPES = {
    EventType.HIGH_VALUE_LEAD.value,
    EventType.DEMO_REQUESTED.value,
    EventType.HUMAN_HANDOFF_REQUESTED.value,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_notification_provider(config: Settings = settings) -> NotificationProvider | None:
    """Construct the configured notification provider, or None if neither is configured.

    Resend (HTTPS API) is preferred whenever it's configured — SMTP is
    never even attempted in that case, since Render's outbound network
    cannot reach smtp.gmail.com:587 (see the SMTP connectivity diagnostic
    and docs/business-actions.md). Falls back to the pre-existing SMTP
    provider only when Resend isn't configured, so any deployment still
    relying on SMTP keeps working unchanged. Deliberately returns None
    rather than raising when neither is configured: missing/partial
    configuration must never prevent the application from starting or the
    chat endpoint from working (Phase 8 spec section 37).
    """
    if config.resend_configured:
        return ResendNotificationProvider(
            api_key=config.RESEND_API_KEY,
            from_email=config.RESEND_FROM_EMAIL,
            to_email=config.OWNER_NOTIFICATION_EMAIL,
        )
    if not config.notifications_configured:
        return None
    return SMTPNotificationProvider(
        host=config.SMTP_HOST,
        port=config.SMTP_PORT,
        username=config.SMTP_USERNAME,
        password=config.SMTP_PASSWORD,
        from_email=config.SMTP_FROM_EMAIL or config.SMTP_USERNAME or config.OWNER_NOTIFICATION_EMAIL,
        to_email=config.OWNER_NOTIFICATION_EMAIL,
        use_tls=config.SMTP_USE_TLS,
    )


@lru_cache
def get_notification_service() -> NotificationService:
    """FastAPI dependency, mirroring `ai.orchestrator.get_orchestrator`.

    Cached for the process lifetime (config doesn't change at runtime);
    tests override this via `app.dependency_overrides` with a fake
    provider, exactly like `get_orchestrator` is overridden elsewhere.
    """
    return NotificationService(build_notification_provider())


def _should_attempt_notification(event_type: str) -> bool:
    if event_type in ALWAYS_NOTIFIABLE_EVENT_TYPES:
        return True
    if event_type == EventType.LEAD_CREATED.value:
        return settings.NOTIFY_ON_LEAD_CREATED
    return False


def _attempt_notification(db: Session, event: BusinessEvent, notification_service: NotificationService) -> None:
    """Attempt delivery for one freshly-created event and record the outcome.

    Never raises: a notification failure must never break the chat request
    that triggered it (Phase 8 spec section 19). Only a genuine
    `db.commit()` failure can propagate, and that's a real infrastructure
    problem the caller should surface just like any other DB write in this
    pipeline.
    """
    if not _should_attempt_notification(event.event_type):
        # Recorded for audit, notification intentionally not attempted —
        # left as PENDING rather than COMPLETED, since no delivery occurred.
        logger.info("business event recorded without notification attempt: %s", event.event_type)
        db.add(event)
        db.commit()
        return

    event.status = EventStatus.PROCESSING.value
    db.add(event)
    db.commit()
    logger.info("notification attempt started: event_type=%s event_id=%s", event.event_type, event.id)

    try:
        notification_service.notify(event.event_type, event.payload, event.created_at)
    except NotificationError as exc:
        # NotificationError messages are always self-authored by this
        # codebase (see notifications.py) — never a raw provider exception
        # — so they're always safe to persist.
        event.status = EventStatus.FAILED.value
        event.error_message = str(exc)[:500]
        logger.warning(
            "notification failed: event_type=%s event_id=%s reason=%s",
            event.event_type,
            event.id,
            event.error_message,
        )
    except Exception as exc:  # noqa: BLE001 - any unexpected provider failure must be caught, never propagate
        # Defense in depth: an exception type we didn't anticipate might
        # embed transport details we don't control — never persist its raw
        # text, only a generic, safe description.
        event.status = EventStatus.FAILED.value
        event.error_message = f"Notification failed due to an unexpected {exc.__class__.__name__}."
        logger.warning(
            "notification failed: event_type=%s event_id=%s reason=%s",
            event.event_type,
            event.id,
            event.error_message,
        )
    else:
        event.status = EventStatus.COMPLETED.value
        event.error_message = None
        logger.info("notification succeeded: event_type=%s event_id=%s", event.event_type, event.id)

    event.processed_at = _utcnow()
    db.add(event)
    db.commit()


def evaluate_and_persist_events(
    db: Session,
    conversation: Conversation,
    state: ConversationState,
    qualification: LeadQualification,
    lead: Lead | None,
    notification_service: NotificationService,
) -> list[BusinessEvent]:
    """Evaluate deterministic business-event rules and persist any new ones.

    Idempotent via `BusinessEvent.dedupe_key` (unique column): re-evaluating
    an already-satisfied condition (e.g. `demo_requested` staying true for
    the rest of the conversation) finds the existing row and skips it —
    exactly one event, and at most one notification attempt, per
    real-world occurrence. Only genuinely new events attempt a
    notification here.
    """
    created: list[BusinessEvent] = []
    for pending in evaluate_events(conversation, state, qualification, lead):
        existing = db.query(BusinessEvent).filter_by(dedupe_key=pending.dedupe_key).first()
        if existing is not None:
            continue

        event = BusinessEvent(
            conversation_id=conversation.id,
            lead_id=pending.lead_id,
            event_type=pending.event_type,
            dedupe_key=pending.dedupe_key,
            payload=pending.payload,
            status=EventStatus.PENDING.value,
        )
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            # Race with another request for the same dedupe_key — someone
            # else already recorded this event; that's success, not error.
            db.rollback()
            continue
        db.refresh(event)
        logger.info("business event created: event_type=%s event_id=%s conversation_id=%s", event.event_type, event.id, conversation.id)
        created.append(event)

    for event in created:
        _attempt_notification(db, event, notification_service)

    return created


# Visitor-facing event types — the only ones ever phrased to the visitor
# via chat context. lead_created/high_value_lead are internal-only signals
# and never described to the visitor either way.
_VISITOR_FACING_EVENT_LABELS = {
    EventType.DEMO_REQUESTED.value: "demo request",
    EventType.HUMAN_HANDOFF_REQUESTED.value: "request to speak with a human",
}


def build_notification_status_context_lines(db: Session, conversation: Conversation) -> list[str]:
    """Ground the AI's reply in the *actual*, current notification outcome.

    Must be called after `evaluate_and_persist_events()` in the same
    request, so this turn's own notification attempt (if any) is already
    reflected in `business_events`. Queried fresh from the database — not
    just from events created this turn — so a later message in the same
    conversation ("did you tell them?") still reflects the true status
    even on a turn that created no new event.

    This exists because `BRILYX_CORE_PROMPT`'s rule ("Never claim the
    Brilyx team was notified unless the backend actually confirms it") has
    nothing to check itself against without this: the model has no other
    way to know whether the owner notification actually succeeded. One
    line per visitor-facing event type actually present for this
    conversation; conversations with neither a demo nor a handoff request
    get an empty list (i.e. no context change at all).
    """
    if not conversation.id:
        return []
    rows = (
        db.query(BusinessEvent)
        .filter(
            BusinessEvent.conversation_id == conversation.id,
            BusinessEvent.event_type.in_(list(_VISITOR_FACING_EVENT_LABELS)),
        )
        .all()
    )
    lines: list[str] = []
    for row in rows:
        label = _VISITOR_FACING_EVENT_LABELS[row.event_type]
        if row.status == EventStatus.COMPLETED.value:
            lines.append(f"Backend CONFIRMED: the Brilyx team has been notified by email about this {label}.")
        else:
            lines.append(
                f"Backend has NOT confirmed the Brilyx team was notified about this {label} — "
                "do not tell the visitor the team has been notified or will be contacted; "
                "say only that the request has been recorded."
            )
    return lines
