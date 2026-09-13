import re

from backend.app.leads.models import ExtractedContactFields, LeadStatus

# ---------------------------------------------------------------------------
# Deterministic contact-signal extraction. No LLM call — see Phase 6 spec
# section 9 ("Do not use Ollama merely to extract a simple name if
# deterministic extraction is sufficient") and section 42 (performance: no
# additional LLM call for lead capture). All matching happens against the
# raw visitor message; callers must never pass assistant text (see
# leads/service.py::_user_message_texts equivalent contract).
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._%+-]*[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}"
)

# A number-shaped token: optional leading '+', then digits/dashes/spaces,
# at least 7 digits total (checked separately below, since this class alone
# can't count digits). Deliberately loose on formatting — normalization
# below only strips whitespace/dashes, never invents or reorders digits.
PHONE_PATTERN = re.compile(r"(\+?\d[\d\-\s]{5,17}\d)")

# Bare domains, "www." domains, and full http(s) URLs. Conservative: requires
# a real-looking TLD (2+ letters), so ordinary sentences don't match.
WEBSITE_PATTERN = re.compile(
    r"\b((?:https?://)?(?:www\.)?[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,})\b"
)

WHATSAPP_KEYWORD_PATTERN = re.compile(r"\bwhatsapp\b", re.IGNORECASE)

# Ordered from most to least explicit. Each captures 1-3 name-shaped words.
NAME_PATTERNS = [
    re.compile(r"\bmy name is\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})", re.IGNORECASE),
    re.compile(r"\byou can call me\s+([A-Za-z][A-Za-z'\-]*)", re.IGNORECASE),
    re.compile(r"\bcall me\s+([A-Za-z][A-Za-z'\-]*)", re.IGNORECASE),
    re.compile(r"\bi'?m\s+([A-Za-z][A-Za-z'\-]*)\b", re.IGNORECASE),
    re.compile(r"\bi am\s+([A-Za-z][A-Za-z'\-]*)\b", re.IGNORECASE),
]

# "I'm ..." / "I am ..." is the most ambiguous pattern (e.g. "I'm looking
# for...", "I'm interested...") — reject a captured word that is one of
# these common non-name continuations rather than guess (Phase 6 spec
# section 9: "If a case is ambiguous, leave the field unchanged").
NAME_STOPWORDS = {
    "looking", "interested", "trying", "here", "ready", "sure", "going",
    "happy", "excited", "not", "just", "also", "still", "thinking",
    "running", "working", "planning", "hoping", "wondering", "curious",
    "confused", "done", "good", "fine", "new", "glad", "afraid", "sorry",
    "a", "an", "the", "with", "from", "on", "in", "at",
}

# The multi-word patterns ("my name is X Y Z") greedily allow up to 3
# name-shaped words so full names are captured — but a message like "My
# name is Ahmed and my email is ..." would otherwise swallow "and my" as
# part of the name. Truncate the capture at the first connector word.
CONNECTOR_STOPWORDS = {
    "and", "but", "or", "so", "my", "is", "am", "are", "the", "a", "an",
    "i", "you", "we", "that", "this", "for", "with", "from", "email",
    "whatsapp", "website", "phone", "number",
}

MIN_PHONE_DIGITS = 7

# Deterministic trigger/status thresholds (Phase 6 spec sections 22, 28).
LEAD_QUALIFIED_SCORE_THRESHOLD = 60


def _extract_email(message: str) -> str | None:
    match = EMAIL_PATTERN.search(message)
    return match.group(0) if match else None


def _extract_website(message_without_email: str) -> str | None:
    match = WEBSITE_PATTERN.search(message_without_email)
    if not match:
        return None
    raw = match.group(1)
    if raw.lower().startswith(("http://", "https://")):
        return raw
    return f"https://{raw}"


def _extract_whatsapp(message: str) -> str | None:
    """Only captures a number when the visitor explicitly calls it WhatsApp.

    "I want a WhatsApp AI agent" contains no digits, so it never matches —
    discussing the product is structurally distinct from providing a
    contact number (Phase 6 spec section 7). A bare phone number with no
    "whatsapp" mention is deliberately never captured here either.
    """
    if not WHATSAPP_KEYWORD_PATTERN.search(message):
        return None
    match = PHONE_PATTERN.search(message)
    if not match:
        return None
    normalized = re.sub(r"[\s\-]", "", match.group(1))
    if len(re.sub(r"\D", "", normalized)) < MIN_PHONE_DIGITS:
        return None
    return normalized


def _extract_name(message: str) -> str | None:
    for pattern in NAME_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        words = match.group(1).strip().split()
        if words[0].lower() in NAME_STOPWORDS:
            continue
        kept: list[str] = []
        for word in words:
            if word.lower() in CONNECTOR_STOPWORDS:
                break
            kept.append(word)
        if not kept:
            continue
        return " ".join(word.capitalize() for word in kept)
    return None


def extract_contact_fields(message: str) -> ExtractedContactFields:
    """Deterministic contact-signal extraction from ONE visitor message.

    Must only ever be called with visitor/user message text (see
    leads/service.py). Website extraction runs against the message with any
    matched email removed first, so an email's domain is never
    double-counted as a website.
    """
    email = _extract_email(message)
    working_text = message.replace(email, "", 1) if email else message
    return ExtractedContactFields(
        name=_extract_name(message),
        email=email,
        whatsapp=_extract_whatsapp(message),
        website=_extract_website(working_text),
    )


def should_create_lead(state, contact_fields: ExtractedContactFields, score: int) -> bool:
    """Whether this conversation now has enough signal to become a Lead.

    Deliberately conservative (Phase 6 spec section 22): a bare "Hi" or a
    generic services question must never create a row. `state` is read
    defensively via getattr so a stub/partial object never crashes this.
    """
    if contact_fields.email or contact_fields.whatsapp:
        return True
    if getattr(state, "demo_requested", False):
        return True
    if getattr(state, "human_handoff_requested", False):
        return True
    if score >= LEAD_QUALIFIED_SCORE_THRESHOLD:
        return True
    return False


def compute_lead_status(current_status: str | None, state, score: int) -> str:
    """Deterministic status assignment. Never sets contacted/demo_sent/converted/lost.

    An existing `do_not_contact` status is never overridden by this
    function — see Phase 6 spec section 30.
    """
    if current_status == LeadStatus.DO_NOT_CONTACT.value:
        return current_status
    if getattr(state, "demo_requested", False):
        return LeadStatus.DEMO_REQUESTED.value
    if getattr(state, "human_handoff_requested", False):
        return LeadStatus.QUALIFIED.value
    if score >= LEAD_QUALIFIED_SCORE_THRESHOLD:
        return LeadStatus.QUALIFIED.value
    return LeadStatus.NEW.value


def compute_notes(state) -> list[str]:
    """Short, deterministic, system-generated notes only — never free text."""
    notes = ["Captured from Brilyx website chatbot."]
    if getattr(state, "demo_requested", False):
        notes.append("Visitor requested a demo.")
    if getattr(state, "human_handoff_requested", False):
        notes.append("Human handoff requested.")
    return notes
