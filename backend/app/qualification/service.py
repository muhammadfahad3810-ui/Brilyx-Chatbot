from sqlalchemy.orm import Session

from backend.app.intelligence.service import get_state_or_default
from backend.app.models import Conversation, ConversationState, LeadQualification
from backend.app.qualification.models import LeadQualificationResult
from backend.app.qualification.rules import calculate_qualification


def _user_message_texts(conversation: Conversation) -> list[str]:
    """The visitor's own message text only — never the assistant's replies.

    This is what keeps the pricing signal visitor-driven (Phase 5 spec
    section 4/5.B): the assistant mentioning pricing must never be scored.
    """
    return [message.content for message in conversation.messages if message.role == "user"]


def get_or_create_qualification(db: Session, conversation: Conversation) -> LeadQualification:
    if conversation.qualification is not None:
        return conversation.qualification
    qualification = LeadQualification(conversation_id=conversation.id)
    db.add(qualification)
    db.commit()
    db.refresh(qualification)
    return qualification


def get_qualification_or_default(db: Session, conversation: Conversation) -> LeadQualificationResult:
    """Read-only, always-fresh qualification for a conversation.

    Recomputed live from the current ConversationState and message history
    rather than trusting the persisted cache — this guarantees a correct
    answer even if `run_qualification` hasn't run yet for this conversation
    (e.g. it has no messages at all), mirroring
    `intelligence.service.get_state_or_default`.
    """
    state = get_state_or_default(db, conversation)
    return calculate_qualification(state, _user_message_texts(conversation))


def run_qualification(db: Session, conversation: Conversation, state: ConversationState) -> LeadQualification:
    """Recalculate and persist the qualification cache after a chat turn.

    Never raises for a scoring failure (mirrors
    `intelligence.service.run_intelligence`): qualification is supplementary
    business intelligence, so a bad computation must never break message
    delivery. Only a genuine database failure (on commit) propagates.
    """
    qualification = get_or_create_qualification(db, conversation)
    try:
        result = calculate_qualification(state, _user_message_texts(conversation))
        qualification.score = result.score
        qualification.level = result.level.value
        qualification.reasons = result.reasons
    except Exception:
        pass
    db.add(qualification)
    db.commit()
    db.refresh(qualification)
    return qualification
