import re

from backend.app.intelligence.models import (
    BusinessType,
    Channel,
    ExtractedFields,
    Intent,
    PackageTier,
    RequestedService,
)

# ---------------------------------------------------------------------------
# Pattern tables. All matching happens against a lowercased copy of the
# visitor's message. These are deliberately simple keyword/regex checks —
# "obvious signals" per Phase 4 scope — not an NLP pipeline.
# ---------------------------------------------------------------------------

HUMAN_HANDOFF_PATTERNS = [
    r"\btalk to (a |an )?(human|person|someone|agent)\b",
    r"\bspeak (to|with) (a |an )?(human|person|someone|agent|your team)\b",
    r"\bconnect me (with|to) (your team|someone|a human)\b",
    r"\breal (person|human)\b",
    r"\bhuman (agent|support|representative)\b",
    r"\bcan i (talk|speak) to (someone|a human|a person)\b",
]

DEMO_PATTERNS = [
    r"\bdemo\b",
    r"\bsee (it|this) in action\b",
    r"\btry it\b",
    r"\bshow me how it works\b",
    r"\bcan i test\b",
    r"\bfree trial\b",
]

PRICING_PATTERNS = [
    r"\bhow much\b",
    r"\bprice(s|ing)?\b",
    r"\bcost(s)?\b",
    r"\bper month\b",
    r"\bfees?\b",
    r"\bcharges?\b",
    r"\bquote\b",
]

GREETING_PATTERN = re.compile(r"^\s*(hi|hello|hey|good morning|good afternoon|good evening|greetings)\s*[!.]*\s*$")

SERVICES_GENERIC_PATTERNS = [
    r"\bwhat services\b",
    r"\bwhat do you offer\b",
    r"\bwhat can you do\b",
    r"\bservices (do|does) (you|brilyx) (offer|provide)\b",
]

CHATBOT_KEYWORDS = ["chatbot", "website bot", "website chat", "chat bot", "site bot"]

WEBSITE_SOFTWARE_KEYWORDS = [
    "website",
    "web app",
    "software",
    "custom software",
    "build me a site",
    "build a website",
    "web page",
    "webpage",
]

REQUEST_VERBS = [
    "i want",
    "i need",
    "looking for",
    "interested in",
    "can you build",
    "get me",
    "set up",
    "i'd like",
    "i would like",
    "please build",
    "need a",
    "want a",
]

WHATSAPP_REQUEST_PATTERN = re.compile(r"\bwhatsapp (ai )?(agent|bot)\b")

CURRENT_USAGE_PATTERNS_WHATSAPP = [
    r"\bwe (get|receive|have) .*whatsapp\b",
    r"\bwhatsapp inquiries\b",
    r"\bquestions .*on whatsapp\b",
    r"\bcustomers .*whatsapp\b",
    r"\bvia whatsapp\b",
    r"\bthrough whatsapp\b",
    r"\bon whatsapp\b",
]

BUSINESS_TYPE_PATTERNS: list[tuple[str, BusinessType]] = [
    (r"\bdental (clinic|practice)\b|\bdentist\b", BusinessType.DENTAL_CLINIC),
    (r"\bmedical clinic\b|\bdoctor'?s office\b|\bphysician\b|\bclinic\b", BusinessType.MEDICAL_CLINIC),
    (r"\bhospital\b|\bhealthcare\b|\bhealth cent(er|re)\b", BusinessType.HEALTHCARE),
    (r"\brestaurant\b|\bdiner\b|\beatery\b", BusinessType.RESTAURANT),
    (r"\bcaf(e|é)\b|\bcoffee shop\b", BusinessType.CAFE),
]

GENERIC_BUSINESS_PATTERN = re.compile(r"\b(my business|my company|we are a|we're a|i run a|i own a|i have a)\b")

PACKAGE_STRICT_PATTERN = re.compile(r"\b(starter|business|pro|custom)\s+(package|plan|tier)\b")
PACKAGE_STRICT_PATTERN_REVERSE = re.compile(r"\b(package|plan|tier)\s+(starter|business|pro|custom)\b")
BUSINESS_AUTOMATION_PATTERN = re.compile(r"\bbusiness\s+automation\b")
AUTOMATION_WORD_PATTERN = re.compile(r"\b(automate|automation|automating)\b")

CORRECTION_PATTERNS = [
    r"\bactually\b",
    r"\bno,? wait\b",
    r"\bi meant\b",
    r"\bcorrection\b",
    r"\binstead\b",
    r"\bsorry,? i meant\b",
]

# Deliberately small and conservative: only well-known names/aliases. Anything
# not in this table stays unknown rather than guessed (see Phase 4 spec: "Do
# NOT infer country purely from language"). Brilyx pricing only distinguishes
# Pakistan (PKR) from everywhere else (USD), so every non-Pakistan entry maps
# to USD.
COUNTRY_CURRENCY_MAP: dict[str, tuple[str, str]] = {
    "pakistan": ("Pakistan", "PKR"),
    "karachi": ("Pakistan", "PKR"),
    "lahore": ("Pakistan", "PKR"),
    "islamabad": ("Pakistan", "PKR"),
    "rawalpindi": ("Pakistan", "PKR"),
    "dubai": ("United Arab Emirates", "USD"),
    "abu dhabi": ("United Arab Emirates", "USD"),
    "uae": ("United Arab Emirates", "USD"),
    "united arab emirates": ("United Arab Emirates", "USD"),
    "usa": ("United States", "USD"),
    "united states": ("United States", "USD"),
    "america": ("United States", "USD"),
    "uk": ("United Kingdom", "USD"),
    "united kingdom": ("United Kingdom", "USD"),
    "canada": ("Canada", "USD"),
    "australia": ("Australia", "USD"),
    "india": ("India", "USD"),
    "saudi arabia": ("Saudi Arabia", "USD"),
    "ksa": ("Saudi Arabia", "USD"),
    "qatar": ("Qatar", "USD"),
}


def is_explicit_correction(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in CORRECTION_PATTERNS)


def _detect_country_currency(text: str) -> tuple[str | None, str | None]:
    for key, (country, currency) in COUNTRY_CURRENCY_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            return country, currency
    return None, None


def _detect_business_type(text: str) -> BusinessType | None:
    for pattern, business_type in BUSINESS_TYPE_PATTERNS:
        if re.search(pattern, text):
            return business_type
    if GENERIC_BUSINESS_PATTERN.search(text):
        return BusinessType.OTHER_BUSINESS
    return None


def _detect_requested_package(text: str, pricing_matched: bool) -> PackageTier | None:
    match = PACKAGE_STRICT_PATTERN.search(text)
    if match:
        return PackageTier(match.group(1))
    match = PACKAGE_STRICT_PATTERN_REVERSE.search(text)
    if match:
        return PackageTier(match.group(2))
    if pricing_matched:
        # Conservative fallback: only fire for tier names that aren't
        # ambiguous with other phrasing ("business" alone is excluded here —
        # it requires the stricter "business package/plan/tier" pattern
        # above, since bare "business" is too generic/ambiguous on its own).
        for word in ("starter", "pro", "custom"):
            if re.search(rf"\b{word}\b", text) and not re.search(rf"\b{word}\s+(software|solution|built|automation)\b", text):
                return PackageTier(word)
    return None


def _whatsapp_is_request(text: str) -> bool:
    if WHATSAPP_REQUEST_PATTERN.search(text):
        return True
    return "whatsapp" in text and any(verb in text for verb in REQUEST_VERBS)


def _whatsapp_is_current_channel(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in CURRENT_USAGE_PATTERNS_WHATSAPP)


def apply_deterministic_rules(message: str) -> ExtractedFields:
    """Detect obvious, high-confidence signals from a single visitor message.

    Returns an ExtractedFields with only the fields this pass is confident
    about set; everything else is left at its default (None/False/[]). The
    overall `confidence` is the max confidence across every signal found —
    see docs/conversation-intelligence.md for why that value gates whether
    an AI extraction call is made at all.
    """
    text = message.lower().strip()
    fields: dict = {}
    confidence = 0.0

    if any(re.search(pattern, text) for pattern in HUMAN_HANDOFF_PATTERNS):
        fields["intent"] = Intent.HUMAN_HANDOFF
        fields["human_handoff_requested"] = True
        confidence = max(confidence, 0.95)

    if any(re.search(pattern, text) for pattern in DEMO_PATTERNS):
        fields.setdefault("intent", Intent.DEMO)
        fields["demo_requested"] = True
        confidence = max(confidence, 0.9)

    pricing_matched = any(re.search(pattern, text) for pattern in PRICING_PATTERNS)
    if pricing_matched:
        fields.setdefault("intent", Intent.PRICING)
        confidence = max(confidence, 0.85)

    package = _detect_requested_package(text, pricing_matched)
    if package:
        fields["requested_package"] = package
        fields.setdefault("intent", Intent.PRICING)
        confidence = max(confidence, 0.9)

    # "Business" is deliberately handled by the strict package pattern above
    # (business+package/plan/tier) and this automation pattern separately —
    # see docs/conversation-intelligence.md for the "Business" ambiguity.
    if BUSINESS_AUTOMATION_PATTERN.search(text) or AUTOMATION_WORD_PATTERN.search(text):
        fields["requested_service"] = RequestedService.BUSINESS_AUTOMATION
        fields.setdefault("intent", Intent.AUTOMATION)
        confidence = max(confidence, 0.75)

    if "whatsapp" in text:
        if _whatsapp_is_request(text):
            fields["requested_service"] = RequestedService.WHATSAPP_AI_AGENT
            fields.setdefault("intent", Intent.WHATSAPP)
            confidence = max(confidence, 0.85)
        elif _whatsapp_is_current_channel(text):
            fields["current_channel"] = Channel.WHATSAPP
            fields.setdefault("intent", Intent.BUSINESS_PROBLEM)
            fields.setdefault("problem_summary", "High volume of customer inquiries via WhatsApp")
            confidence = max(confidence, 0.75)
        else:
            fields.setdefault("intent", Intent.WHATSAPP)
            confidence = max(confidence, 0.6)

    if any(keyword in text for keyword in CHATBOT_KEYWORDS):
        fields["requested_service"] = RequestedService.AI_WEBSITE_CHATBOT
        fields.setdefault("intent", Intent.CHATBOT)
        confidence = max(confidence, 0.85)

    if "requested_service" not in fields and any(keyword in text for keyword in WEBSITE_SOFTWARE_KEYWORDS):
        fields["requested_service"] = RequestedService.WEBSITES_SOFTWARE
        fields.setdefault("intent", Intent.WEBSITE_SOFTWARE)
        confidence = max(confidence, 0.8)

    if "requested_service" not in fields and re.search(r"\bcustom (solution|built solution)\b", text):
        fields["requested_service"] = RequestedService.CUSTOM_SOLUTION
        confidence = max(confidence, 0.75)

    if "intent" not in fields and any(re.search(pattern, text) for pattern in SERVICES_GENERIC_PATTERNS):
        fields["intent"] = Intent.SERVICES
        confidence = max(confidence, 0.85)

    business_type = _detect_business_type(text)
    if business_type:
        fields["business_type"] = business_type
        fields.setdefault("intent", Intent.BUSINESS_PROBLEM)
        confidence = max(confidence, 0.6 if business_type == BusinessType.OTHER_BUSINESS else 0.7)

    country, currency = _detect_country_currency(text)
    if country:
        fields["country"] = country
        fields["currency"] = currency
        confidence = max(confidence, 0.9)

    if "intent" not in fields and GREETING_PATTERN.match(text):
        fields["intent"] = Intent.GREETING
        confidence = max(confidence, 0.9)

    fields["confidence"] = confidence
    return ExtractedFields(**fields)
