import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from backend.app.events.models import EventType

REQUEST_TIMEOUT_SECONDS = 10


class NotificationError(Exception):
    """Raised when a notification could not be delivered, in a controlled way.

    Deliberately never constructed from a raw provider exception's `str()`
    (see `SMTPNotificationProvider.send`) — its message is always something
    this codebase wrote itself, so it's always safe to persist to
    `BusinessEvent.error_message` without risking an SMTP password or other
    transport secret leaking into the database.
    """


class NotificationProvider(ABC):
    """Abstraction every notification transport must implement.

    Mirrors `backend.app.ai.base.AIProvider`: the rest of the application
    only ever talks to this interface, so a new transport (a different SMTP
    library, a transactional-email API, ...) can be added without touching
    `NotificationService` or the business-event layer above it.
    """

    @abstractmethod
    def send(self, subject: str, body: str) -> None:
        """Deliver one plain-text notification to the configured owner address.

        Raises:
            NotificationError: if delivery is not possible for any expected
                reason (not configured, auth failure, connection failure,
                timeout, ...).
        """
        raise NotImplementedError


class SMTPNotificationProvider(NotificationProvider):
    """Standard-library `smtplib` transport. No third-party email SDK, no paid service."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        to_email: str,
        use_tls: bool = True,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_email = from_email
        self._to_email = to_email
        self._use_tls = use_tls

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._from_email
        message["To"] = self._to_email
        message.set_content(body)

        try:
            with smtplib.SMTP(self._host, self._port, timeout=REQUEST_TIMEOUT_SECONDS) as server:
                if self._use_tls:
                    server.starttls()
                if self._username:
                    server.login(self._username, self._password)
                server.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            # Never include str(exc) here: some SMTP servers echo the
            # attempted credentials back in the auth-failure response.
            raise NotificationError("SMTP authentication failed") from exc
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise NotificationError(f"SMTP delivery failed ({exc.__class__.__name__})") from exc


# ---------------------------------------------------------------------------
# Plain-text email rendering. Deliberately never HTML: visitor-provided
# fields (name, business name, requirements, ...) are untrusted, and plain
# text has no injection surface to sanitize against (Phase 8 spec section
# 20). Subject lines are always a fixed string per event type — visitor
# content only ever appears in the body — so there is no header-injection
# risk from a visitor-controlled Subject line either.
# ---------------------------------------------------------------------------

_SUBJECTS = {
    EventType.LEAD_CREATED.value: "New Lead Captured — Brilyx",
    EventType.HIGH_VALUE_LEAD.value: "High-Value Lead — Brilyx",
    EventType.DEMO_REQUESTED.value: "Demo Requested — Brilyx",
    EventType.HUMAN_HANDOFF_REQUESTED.value: "Human Handoff Requested — Brilyx",
}

_INTRO = {
    EventType.LEAD_CREATED.value: "A new lead was captured by the Brilyx website chatbot.",
    EventType.HIGH_VALUE_LEAD.value: "A lead has reached a HIGH qualification score.",
    EventType.DEMO_REQUESTED.value: (
        "A visitor requested a demo. No demo has been created or sent — "
        "this is only a notification of the request."
    ),
    EventType.HUMAN_HANDOFF_REQUESTED.value: (
        "A visitor asked to speak with a human. No one has been notified "
        "of a response yet — this is only a notification of the request."
    ),
}

_FIELD_LABELS = [
    ("name", "Name"),
    ("business_name", "Business name"),
    ("business_type", "Business type"),
    ("country", "Country"),
    ("requested_service", "Requested service"),
    ("requested_package", "Requested package"),
    ("problem_summary", "Problem summary"),
    ("requirements", "Requirements"),
    ("email", "Email"),
    ("whatsapp", "WhatsApp"),
    ("website", "Website"),
    ("lead_score", "Lead score"),
    ("lead_level", "Lead level"),
    ("lead_status", "Lead status"),
    ("conversation_id", "Conversation ID"),
]


def render_event_email(event_type: str, payload: dict, created_at) -> tuple[str, str]:
    """Build a plain-text (subject, body) pair for one business event.

    `payload` values are visitor-derived and untrusted, but since the body
    is plain text (never interpreted as markup) there is nothing to escape
    — the value is simply written out as-is, one field per line.
    """
    subject = _SUBJECTS.get(event_type, "Brilyx Notification")
    lines = [_INTRO.get(event_type, "A Brilyx business event occurred."), ""]
    for field, label in _FIELD_LABELS:
        value = payload.get(field)
        if field == "requirements":
            value = "; ".join(value) if value else None
        if value in (None, "", "unknown"):
            continue
        lines.append(f"{label}: {value}")
    lines.append(f"Timestamp: {created_at.isoformat()}")
    return subject, "\n".join(lines)


class NotificationService:
    """Maps a BusinessEvent to a rendered email and hands it to the configured provider.

    `provider` is `None` when SMTP/owner-email configuration is incomplete
    (see `events.service.build_notification_provider`) — in that case
    `notify()` always raises `NotificationError`, which the caller records
    as a failed event rather than ever crashing the chat request.
    """

    def __init__(self, provider: NotificationProvider | None):
        self._provider = provider

    @property
    def is_configured(self) -> bool:
        return self._provider is not None

    def notify(self, event_type: str, payload: dict, created_at) -> None:
        if self._provider is None:
            raise NotificationError("Notifications are not configured")
        subject, body = render_event_email(event_type, payload, created_at)
        self._provider.send(subject, body)
