import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    session_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=_new_uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ConversationStatus.ACTIVE.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )
    state: Mapped["ConversationState | None"] = relationship(
        "ConversationState",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    qualification: Mapped["LeadQualification | None"] = relationship(
        "LeadQualification",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    lead: Mapped["Lead | None"] = relationship(
        "Lead",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    events: Mapped[list["BusinessEvent"]] = relationship(
        "BusinessEvent",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="BusinessEvent.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")


class ConversationState(Base):
    """The cumulative, structured understanding of a conversation.

    One-to-one with Conversation (conversation_id is both PK and FK). This
    is conversation *understanding*, not a lead record — see
    docs/conversation-intelligence.md. Enum-like fields are stored as plain
    strings (their `.value`); `requirements` is a JSON array of short
    strings, since SQLite has no native array type.
    """

    __tablename__ = "conversation_states"

    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), primary_key=True)

    intent: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    business_type: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(60), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    current_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requested_service: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    requested_package: Mapped[str | None] = mapped_column(String(16), nullable=True)
    problem_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    demo_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    human_handoff_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    handoff_recommended: Mapped[bool] = mapped_column(default=False, nullable=False)
    correction_count: Mapped[int] = mapped_column(default=0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="state")


class LeadQualification(Base):
    """Cached deterministic qualification score, one-to-one with Conversation.

    This is qualification, not lead capture — no contact details live here.
    score/level/reasons are always reproducible from ConversationState + the
    conversation's own message history (see backend/app/qualification/);
    this row is a persisted cache for analytics/debugging, never itself the
    source of truth. See docs/lead-qualification.md.
    """

    __tablename__ = "lead_qualifications"

    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), primary_key=True)
    score: Mapped[int] = mapped_column(default=0, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="qualification")


class Lead(Base):
    """A structured business-lead snapshot, at most one per Conversation.

    This is the Phase 6 lead record — see docs/lead-capture.md. Contact
    fields (`name`/`email`/`whatsapp`/`website`) are captured here directly
    from visitor messages; business/intelligence fields
    (`business_type`/`business_name`/`country`/`preferred_currency`/
    `requested_service`/`estimated_package`/`problem_summary`/
    `requirements`) are a copied snapshot of the conversation's
    ConversationState, never re-derived independently. `lead_score`/
    `lead_level` come from the Phase 5 deterministic qualification system —
    never client-supplied, never model-generated.

    `conversation_id` is unique (not the primary key, unlike
    ConversationState/LeadQualification) because a Lead may not exist for
    every conversation, and gaining a lead later shouldn't require row
    replacement.
    """

    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    business_type: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    country: Mapped[str | None] = mapped_column(String(60), nullable=True)
    preferred_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)

    requested_service: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    problem_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    estimated_package: Mapped[str | None] = mapped_column(String(16), nullable=True)

    lead_score: Mapped[int] = mapped_column(default=0, nullable=False)
    lead_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    lead_status: Mapped[str] = mapped_column(String(16), default="new", nullable=False)

    source: Mapped[str] = mapped_column(String(32), default="website_chatbot", nullable=False)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="lead")


class BusinessEvent(Base):
    """An auditable record of a backend-decided business action, at most one per dedupe_key.

    This is the Phase 8 event/notification layer — see
    docs/business-actions.md. A BusinessEvent is never created by the AI or
    by the visitor; it is always the deterministic output of
    `backend/app/events/rules.py` evaluating ConversationState +
    LeadQualification + Lead, persisted here so a repeated identical
    condition (e.g. the visitor sending another message after already
    requesting a demo) can be recognized and skipped via `dedupe_key`
    rather than re-notifying.

    `payload` is a bounded snapshot of the fields needed to write a useful
    notification — never the full conversation, never a secret.
    """

    __tablename__ = "business_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), index=True, nullable=False
    )
    lead_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("leads.id"), index=True, nullable=True)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="events")
