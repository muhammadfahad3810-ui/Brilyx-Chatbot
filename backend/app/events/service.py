import logging
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import Settings, settings
from backend.app.events.models import EventStatus, EventType
from backend.app.events.notifications import NotificationError, NotificationService, SMTPNotificationProvider
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


def build_notification_provider(config: Settings = settings) -> SMTPNotificationProvider | None:
    """Construct the configured SMTP provider, or None if not fully configured.

    Deliberately returns None rather than raising: missing/partial SMTP
    configuration must never prevent the application from starting or the
    chat endpoint from working (Phase 8 spec section 37).
    """
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
