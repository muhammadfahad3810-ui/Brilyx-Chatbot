from sqlalchemy.orm import Session

from backend.app.intelligence.rules import is_explicit_correction
from backend.app.leads.models import ExtractedContactFields
from backend.app.leads.rules import compute_lead_status, compute_notes, extract_contact_fields, should_create_lead
from backend.app.models import Conversation, ConversationState, Lead, LeadQualification

MAX_REQUIREMENTS = 10

# Contact fields captured directly by Phase 6 (never derived from
# ConversationState, which has no contact columns — see docs/lead-capture.md).
CONTACT_FIELDS = ("name", "email", "whatsapp", "website")


def _merge_contact_field(lead: Lead, field: str, new_value: str | None, corrected: bool) -> None:
    """Sticky-with-correction merge, mirroring intelligence.service._merge_scalar_field.

    An empty extraction never clears an existing value. Once set, a field
    only changes on an explicit visitor correction ("actually", "I meant",
    ...) — see Phase 6 spec section 26.
    """
    if not new_value:
        return
    current = getattr(lead, field)
    if not current:
        setattr(lead, field, new_value)
        return
    if current == new_value:
        return
    if corrected:
        setattr(lead, field, new_value)


def merge_contact_fields(lead: Lead, fields: ExtractedContactFields, raw_message: str) -> None:
    corrected = is_explicit_correction(raw_message.lower())
    for field in CONTACT_FIELDS:
        _merge_contact_field(lead, field, getattr(fields, field), corrected)


def _sync_lead_from_state(lead: Lead, state: ConversationState) -> None:
    """Copy the relevant ConversationState snapshot onto the Lead.

    Never re-derives these fields independently (Phase 6 spec section 4) —
    ConversationState remains the single source of intelligence truth.
    """
    lead.business_type = state.business_type
    lead.business_name = state.business_name
    lead.country = state.country
    lead.preferred_currency = state.currency
    lead.requested_service = state.requested_service
    lead.problem_summary = state.problem_summary
    lead.requirements = list(state.requirements or [])[:MAX_REQUIREMENTS]
    lead.estimated_package = state.requested_package


def run_lead_capture(
    db: Session,
    conversation: Conversation,
    state: ConversationState,
    qualification: LeadQualification,
    raw_message: str,
) -> Lead | None:
    """Create/update this conversation's single Lead row if lead intent is present.

    `conversation_id` is the deduplication key (Phase 6 spec section 27):
    if `conversation.lead` already exists it is updated in place, never
    duplicated. If it doesn't exist yet, a row is only created when
    `should_create_lead` finds real intent — a bare "Hi" must never create
    one (section 22).

    Never raises for an extraction/scoring-logic failure (mirrors
    `intelligence.service.run_intelligence` /
    `qualification.service.run_qualification`): lead capture is
    supplementary, so it must never break message delivery. Only a genuine
    database failure (on commit) propagates.
    """
    try:
        current_fields = extract_contact_fields(raw_message)
    except Exception:
        current_fields = ExtractedContactFields()

    lead = conversation.lead
    is_new = lead is None
    if is_new:
        if not should_create_lead(state, current_fields, qualification.score):
            return None
        lead = Lead(conversation_id=conversation.id)

    try:
        if is_new:
            # A visitor may state contact info (e.g. a name) in an earlier
            # turn, before anything meets the creation trigger — backfill
            # the full visitor message history once at creation time so
            # that information isn't lost (Phase 6 spec section 25: capture
            # regardless of sequence). `conversation.messages` is already
            # chronological (Message.id order), which also keeps correction
            # detection applying in the right temporal order.
            for message in conversation.messages:
                if message.role != "user":
                    continue
                merge_contact_fields(lead, extract_contact_fields(message.content), message.content)
        else:
            merge_contact_fields(lead, current_fields, raw_message)
        _sync_lead_from_state(lead, state)
        lead.lead_score = qualification.score
        lead.lead_level = qualification.level
        lead.lead_status = compute_lead_status(lead.lead_status, state, qualification.score)
        lead.notes = compute_notes(state)
    except Exception:
        pass

    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead
