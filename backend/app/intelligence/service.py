import re

from sqlalchemy.orm import Session

from backend.app.ai.orchestrator import AIOrchestrator
from backend.app.intelligence.extractor import extract_fields
from backend.app.intelligence.models import ExtractedFields
from backend.app.intelligence.rules import is_explicit_correction
from backend.app.models import Conversation, ConversationState

MAX_REQUIREMENTS = 10
MAX_PROBLEM_SUMMARY_LENGTH = 500
HANDOFF_RECOMMEND_REQUIREMENTS_THRESHOLD = 3
HANDOFF_RECOMMEND_CORRECTIONS_THRESHOLD = 2

DISSATISFACTION_PATTERNS = [
    r"\bnot happy\b",
    r"\bfrustrat",
    r"\bnot working\b",
    r"\bunhappy\b",
    r"\bdisappoint",
]

# Fields that are treated as facts *about the business itself* — once set,
# they only change when the visitor explicitly corrects themselves (e.g.
# "Actually, it's a cafe"). See docs/conversation-intelligence.md.
STICKY_FIELDS = ("business_type", "business_name", "country", "currency")

# Fields that reflect what the visitor is currently asking about — these
# freely update to the latest explicit signal, since asking about a
# different service/package/tier is a new question, not a correction.
TOPIC_FIELDS = ("intent", "requested_service", "requested_package", "current_channel")


def get_or_create_state(db: Session, conversation: Conversation) -> ConversationState:
    if conversation.state is not None:
        return conversation.state
    state = ConversationState(conversation_id=conversation.id)
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def get_state_or_default(db: Session, conversation: Conversation) -> ConversationState:
    """Read-only lookup: never creates a row, just returns a fresh in-memory default.

    Column `default=` values only apply at flush time, so an unpersisted
    ConversationState must have its defaults set explicitly here rather
    than relying on SQLAlchemy to fill them in.
    """
    if conversation.state is not None:
        return conversation.state
    return ConversationState(
        conversation_id=conversation.id,
        intent="unknown",
        business_type="unknown",
        requested_service="unknown",
        requirements=[],
        demo_requested=False,
        human_handoff_requested=False,
        handoff_recommended=False,
        correction_count=0,
        confidence=0.0,
    )


def _is_dissatisfied(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in DISSATISFACTION_PATTERNS)


def _merge_scalar_field(state: ConversationState, field: str, new_value: str | None, *, sticky: bool, corrected: bool) -> bool:
    """Merge one scalar field. Returns True if the stored value changed."""
    if new_value in (None, "unknown"):
        return False
    current = getattr(state, field)
    if current in (None, "unknown"):
        setattr(state, field, new_value)
        return True
    if current == new_value:
        return False
    if sticky and not corrected:
        return False  # established fact, no correction signal — never overwritten
    setattr(state, field, new_value)
    return True


def merge_state(state: ConversationState, fields: ExtractedFields, raw_message: str) -> None:
    """Merge one turn's extracted fields into the cumulative conversation state.

    Rules (see docs/conversation-intelligence.md for the full rationale):
    - Empty fields never overwrite an established value.
    - Sticky fields (business_type/business_name/country/currency) only
      change on an explicit correction ("actually", "I meant", ...).
    - Topic fields (intent/requested_service/requested_package/channel)
      freely update to the latest explicit signal.
    - problem_summary and requirements are additive, never replaced.
    - demo_requested/human_handoff_requested are monotonic (True stays True).
    """
    corrected = is_explicit_correction(raw_message.lower())
    sticky_changed = False

    for field in STICKY_FIELDS:
        value = getattr(fields, field)
        value = value.value if hasattr(value, "value") else value
        if _merge_scalar_field(state, field, value, sticky=True, corrected=corrected):
            sticky_changed = True

    for field in TOPIC_FIELDS:
        value = getattr(fields, field)
        value = value.value if hasattr(value, "value") else value
        _merge_scalar_field(state, field, value, sticky=False, corrected=False)

    if fields.problem_summary:
        if not state.problem_summary:
            state.problem_summary = fields.problem_summary
        elif fields.problem_summary.lower() not in state.problem_summary.lower():
            state.problem_summary = f"{state.problem_summary}; {fields.problem_summary}"[:MAX_PROBLEM_SUMMARY_LENGTH]

    if fields.requirements:
        existing = list(state.requirements or [])
        for item in fields.requirements:
            if item not in existing:
                existing.append(item)
        state.requirements = existing[:MAX_REQUIREMENTS]

    if fields.demo_requested:
        state.demo_requested = True
    if fields.human_handoff_requested:
        state.human_handoff_requested = True

    if sticky_changed and corrected:
        state.correction_count = (state.correction_count or 0) + 1

    if (
        state.requested_package == "custom"
        or state.requested_service == "custom_solution"
        or len(state.requirements or []) >= HANDOFF_RECOMMEND_REQUIREMENTS_THRESHOLD
        or _is_dissatisfied(raw_message.lower())
        or (state.correction_count or 0) >= HANDOFF_RECOMMEND_CORRECTIONS_THRESHOLD
    ):
        state.handoff_recommended = True

    if fields.confidence:
        state.confidence = max(state.confidence or 0.0, fields.confidence)


def build_context_lines(state: ConversationState) -> list[str]:
    """Render the state into short, human-readable lines for the AI's system prompt."""
    lines: list[str] = []
    if state.business_type and state.business_type != "unknown":
        lines.append(f"Business type: {state.business_type.replace('_', ' ').title()}")
    if state.business_name:
        lines.append(f"Business name: {state.business_name}")
    if state.problem_summary:
        lines.append(f"Problem: {state.problem_summary}")
    if state.current_channel and state.current_channel != "unknown":
        lines.append(f"Current communication channel: {state.current_channel.title()}")
    if state.requested_service and state.requested_service != "unknown":
        lines.append(f"Requested service: {state.requested_service.replace('_', ' ').title()}")
        if not state.requested_package:
            # Structural safeguard against inferring a pricing tier from a
            # service name (e.g. "Business Automation" -> "Business" tier)
            # — see docs/conversation-intelligence.md. This line is always
            # present alongside a service whenever no package/tier has
            # actually been identified, regardless of whether the model
            # attends to the system-prompt rule on any given turn.
            lines.append(
                "IMPORTANT: No specific pricing package/tier has been identified "
                "for this service. Give NO price numbers in this reply — not one "
                "tier's number, and not all four tiers' numbers either. Do not "
                "assume or quote a package price for it, and do not list every "
                "package's price as a fallback answer either. Say only that this "
                "service's pricing depends on scope/requirements."
            )
    if state.requested_package:
        lines.append(
            f"Asking about package/tier: {state.requested_package.title()} — "
            "quote its approved setup/monthly numbers now."
        )
    if state.country:
        currency_note = f" (currency: {state.currency})" if state.currency else ""
        lines.append(f"Country: {state.country}{currency_note}")
    if state.requirements:
        lines.append("Stated requirements: " + "; ".join(state.requirements))
    if state.demo_requested:
        lines.append("Visitor has asked about a demo (no demo has actually been created/sent yet).")
    if state.human_handoff_requested:
        lines.append("Visitor has asked to speak with a human.")
    return lines


def run_intelligence(
    db: Session,
    conversation: Conversation,
    message: str,
    orchestrator: AIOrchestrator,
) -> ConversationState:
    """Extract structured signals from `message` and merge/persist them.

    Never raises: any extraction failure leaves the existing state
    untouched (still persisted/returned) rather than breaking the chat
    flow, since intelligence is supplementary to message delivery.
    """
    state = get_or_create_state(db, conversation)
    try:
        fields = extract_fields(orchestrator, message)
        merge_state(state, fields, message)
    except Exception:
        pass
    db.add(state)
    db.commit()
    db.refresh(state)
    return state
