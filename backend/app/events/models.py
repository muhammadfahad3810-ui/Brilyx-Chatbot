from dataclasses import dataclass
from enum import Enum


class EventType(str, Enum):
    LEAD_CREATED = "lead_created"
    HIGH_VALUE_LEAD = "high_value_lead"
    DEMO_REQUESTED = "demo_requested"
    HUMAN_HANDOFF_REQUESTED = "human_handoff_requested"


class EventStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class PendingEvent:
    """A deterministically-decided business event, not yet persisted.

    Pure data — the output of `events.rules.evaluate_events()`. Turning
    this into a real `BusinessEvent` row (and deciding, via `dedupe_key`,
    whether it's actually new) is the DB-facing job of `events.service`.
    """

    event_type: str
    dedupe_key: str
    payload: dict
    lead_id: str | None
