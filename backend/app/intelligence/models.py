from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(str, Enum):
    GREETING = "greeting"
    SERVICES = "services"
    CHATBOT = "chatbot"
    WHATSAPP = "whatsapp"
    AUTOMATION = "automation"
    WEBSITE_SOFTWARE = "website_software"
    PRICING = "pricing"
    DEMO = "demo"
    BUSINESS_PROBLEM = "business_problem"
    SUPPORT = "support"
    CONTACT = "contact"
    HUMAN_HANDOFF = "human_handoff"
    UNKNOWN = "unknown"


class RequestedService(str, Enum):
    AI_WEBSITE_CHATBOT = "ai_website_chatbot"
    WHATSAPP_AI_AGENT = "whatsapp_ai_agent"
    BUSINESS_AUTOMATION = "business_automation"
    WEBSITES_SOFTWARE = "websites_software"
    CUSTOM_SOLUTION = "custom_solution"
    UNKNOWN = "unknown"


class BusinessType(str, Enum):
    DENTAL_CLINIC = "dental_clinic"
    MEDICAL_CLINIC = "medical_clinic"
    HEALTHCARE = "healthcare"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    OTHER_BUSINESS = "other_business"
    UNKNOWN = "unknown"


class PackageTier(str, Enum):
    STARTER = "starter"
    BUSINESS = "business"
    PRO = "pro"
    CUSTOM = "custom"


class Channel(str, Enum):
    WEBSITE = "website"
    WHATSAPP = "whatsapp"
    PHONE = "phone"
    EMAIL = "email"
    UNKNOWN = "unknown"


MAX_PROBLEM_SUMMARY_LENGTH = 300
MAX_REQUIREMENT_LENGTH = 200
MAX_REQUIREMENTS = 10
MAX_BUSINESS_NAME_LENGTH = 120
MAX_COUNTRY_LENGTH = 60


class ExtractedFields(BaseModel):
    """Untrusted, validated output of a single extraction pass (deterministic or AI).

    Every field is optional: it represents only what *this* pass found in
    *this* message, not the cumulative conversation state. Unknown JSON keys
    from a model response are silently ignored rather than rejected outright
    (`extra="ignore"`), and every value is validated/normalized/clamped —
    this is the boundary that keeps untrusted model output from ever
    reaching the database or a prompt unchecked.
    """

    model_config = ConfigDict(extra="ignore")

    intent: Intent | None = None
    business_type: BusinessType | None = None
    business_name: str | None = None
    country: str | None = None
    currency: str | None = None
    current_channel: Channel | None = None
    requested_service: RequestedService | None = None
    requested_package: PackageTier | None = None
    problem_summary: str | None = None
    requirements: list[str] = Field(default_factory=list)
    demo_requested: bool = False
    human_handoff_requested: bool = False
    confidence: float = 0.0

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, value))

    @field_validator("business_name", "country", "problem_summary", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @field_validator("business_name")
    @classmethod
    def truncate_business_name(cls, value: str | None) -> str | None:
        return value[:MAX_BUSINESS_NAME_LENGTH] if value else value

    @field_validator("country")
    @classmethod
    def truncate_country(cls, value: str | None) -> str | None:
        return value[:MAX_COUNTRY_LENGTH] if value else value

    @field_validator("problem_summary")
    @classmethod
    def truncate_problem_summary(cls, value: str | None) -> str | None:
        return value[:MAX_PROBLEM_SUMMARY_LENGTH] if value else value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        value = value.strip().upper()
        return value if value in ("PKR", "USD") else None

    @field_validator("requirements", mode="before")
    @classmethod
    def normalize_requirements(cls, value):
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if item:
                cleaned.append(item[:MAX_REQUIREMENT_LENGTH])
            if len(cleaned) >= MAX_REQUIREMENTS:
                break
        return cleaned
