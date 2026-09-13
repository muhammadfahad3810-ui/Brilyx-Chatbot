import re

from backend.app.intelligence.rules import PRICING_PATTERNS
from backend.app.qualification.models import LeadQualificationResult, QualificationLevel

# ---------------------------------------------------------------------------
# Approved scoring weights (Phase 5 spec, section 3). Deliberately additive
# and fixed — never derived, never adjusted by an LLM. Weights sum to
# exactly 100, so no single combination of signals can exceed the max on
# its own; `_clamp` exists as a structural safeguard regardless (tested
# directly).
# ---------------------------------------------------------------------------

BUSINESS_IDENTIFIED_WEIGHT = 15
CLEAR_PROBLEM_WEIGHT = 20
SPECIFIC_SERVICE_WEIGHT = 15
PRICING_INTEREST_WEIGHT = 10
DEMO_REQUESTED_WEIGHT = 20
EMAIL_PROVIDED_WEIGHT = 10
WHATSAPP_PROVIDED_WEIGHT = 10

MIN_SCORE = 0
MAX_SCORE = 100

LOW_MAX = 29
MEDIUM_MAX = 59

# A problem_summary this short (or matching a known-vague phrase below) is
# treated as "no concrete problem stated yet" — see Phase 5 spec section 4.
MIN_PROBLEM_SUMMARY_LENGTH = 12
MIN_REQUIREMENT_LENGTH = 4

VAGUE_PROBLEM_PHRASES = {
    "i need help",
    "tell me about ai",
    "i want something",
}

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clamp(score: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, score))


def level_for_score(score: int) -> QualificationLevel:
    if score <= LOW_MAX:
        return QualificationLevel.LOW
    if score <= MEDIUM_MAX:
        return QualificationLevel.MEDIUM
    return QualificationLevel.HIGH


def _is_vague_problem(summary: str) -> bool:
    return summary.strip().lower().rstrip(".!?") in VAGUE_PROBLEM_PHRASES


def has_business_identified(state) -> bool:
    """Business identified — a meaningful business type or name is known.

    `state` is normally a ConversationState, but every field is read via
    `getattr(..., default)` so a stub/partial object (as used in tests) or
    a future field rename never crashes scoring — missing information
    simply scores 0 for that signal (Phase 5 spec section 16).
    """
    business_type = getattr(state, "business_type", None)
    if business_type and business_type != "unknown":
        return True
    business_name = getattr(state, "business_name", None)
    return bool(business_name and business_name.strip())


def has_clear_problem(state) -> bool:
    """Clear business problem — a concrete problem_summary or requirement."""
    summary = (getattr(state, "problem_summary", None) or "").strip()
    if summary and len(summary) >= MIN_PROBLEM_SUMMARY_LENGTH and not _is_vague_problem(summary):
        return True
    requirements = getattr(state, "requirements", None) or []
    return any(req and len(req.strip()) >= MIN_REQUIREMENT_LENGTH for req in requirements)


def has_specific_service(state) -> bool:
    """Specific Brilyx service identified — independent of any pricing tier."""
    service = getattr(state, "requested_service", None)
    return bool(service and service != "unknown")


def has_pricing_interest(state, user_messages: list[str]) -> bool:
    """Pricing/budget interest — must be visitor-driven.

    A named package/tier already on the state counts (it can only get there
    from an explicit visitor statement — see intelligence/rules.py). Beyond
    that, only the visitor's own messages are scanned for pricing language;
    anything the assistant said is never consulted, so a reply that merely
    mentions pricing can never award this signal on its own.
    """
    if getattr(state, "requested_package", None):
        return True
    for message in user_messages:
        if not message:
            continue
        lowered = message.lower()
        if any(re.search(pattern, lowered) for pattern in PRICING_PATTERNS):
            return True
    return False


def has_demo_requested(state) -> bool:
    return bool(getattr(state, "demo_requested", False))


def has_email_provided(state) -> bool:
    """Stays False until a real `email` field exists on ConversationState (Phase 6)."""
    email = getattr(state, "email", None)
    if not isinstance(email, str) or not email.strip():
        return False
    return bool(EMAIL_PATTERN.match(email.strip()))


def has_whatsapp_contact_provided(state) -> bool:
    """Stays False until a real contact field exists on ConversationState (Phase 6).

    Deliberately distinct from `requested_service == whatsapp_ai_agent` —
    discussing the WhatsApp AI product is not the same as having a captured
    contact number (Phase 5 spec section 5.A).
    """
    contact = getattr(state, "whatsapp_number", None) or getattr(state, "whatsapp_contact", None)
    if not isinstance(contact, str) or not contact.strip():
        return False
    digits = re.sub(r"\D", "", contact)
    return len(digits) >= 7


def calculate_qualification(state, user_messages: list[str] | None = None) -> LeadQualificationResult:
    """Deterministically score a conversation snapshot. Pure: same inputs, same output.

    No LLM call, no randomness, no hidden mutable state — every signal is
    recomputed fresh from `state` (+ the visitor's own message text for the
    pricing signal) every time this is called, per Phase 5 spec section 11.
    """
    user_messages = user_messages or []
    score = 0
    reasons: list[str] = []

    if has_business_identified(state):
        score += BUSINESS_IDENTIFIED_WEIGHT
        reasons.append(f"Business identified (+{BUSINESS_IDENTIFIED_WEIGHT})")

    if has_clear_problem(state):
        score += CLEAR_PROBLEM_WEIGHT
        reasons.append(f"Clear business problem (+{CLEAR_PROBLEM_WEIGHT})")

    if has_specific_service(state):
        score += SPECIFIC_SERVICE_WEIGHT
        reasons.append(f"Specific service identified (+{SPECIFIC_SERVICE_WEIGHT})")

    if has_pricing_interest(state, user_messages):
        score += PRICING_INTEREST_WEIGHT
        reasons.append(f"Pricing interest (+{PRICING_INTEREST_WEIGHT})")

    if has_demo_requested(state):
        score += DEMO_REQUESTED_WEIGHT
        reasons.append(f"Demo requested (+{DEMO_REQUESTED_WEIGHT})")

    if has_email_provided(state):
        score += EMAIL_PROVIDED_WEIGHT
        reasons.append(f"Email provided (+{EMAIL_PROVIDED_WEIGHT})")

    if has_whatsapp_contact_provided(state):
        score += WHATSAPP_PROVIDED_WEIGHT
        reasons.append(f"WhatsApp contact provided (+{WHATSAPP_PROVIDED_WEIGHT})")

    score = _clamp(score)
    return LeadQualificationResult(score=score, level=level_for_score(score), reasons=reasons)
