from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

MAX_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 254
MAX_WHATSAPP_LENGTH = 32
MAX_WEBSITE_LENGTH = 300


class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    DEMO_REQUESTED = "demo_requested"
    DEMO_SENT = "demo_sent"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    LOST = "lost"
    DO_NOT_CONTACT = "do_not_contact"


class ExtractedContactFields(BaseModel):
    """Untrusted, validated output of one deterministic contact-extraction pass.

    Every field reflects only what was found in *this* visitor message —
    never the assistant's message, never accumulated lead state. This is
    the validation boundary between raw regex matches and anything ever
    written to a `Lead` row. See docs/lead-capture.md.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    website: str | None = None

    @field_validator("name", "email", "whatsapp", "website", mode="before")
    @classmethod
    def _normalize(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @field_validator("name")
    @classmethod
    def _truncate_name(cls, value: str | None) -> str | None:
        return value[:MAX_NAME_LENGTH] if value else value

    @field_validator("email")
    @classmethod
    def _truncate_email(cls, value: str | None) -> str | None:
        return value[:MAX_EMAIL_LENGTH] if value else value

    @field_validator("whatsapp")
    @classmethod
    def _truncate_whatsapp(cls, value: str | None) -> str | None:
        return value[:MAX_WHATSAPP_LENGTH] if value else value

    @field_validator("website")
    @classmethod
    def _truncate_website(cls, value: str | None) -> str | None:
        return value[:MAX_WEBSITE_LENGTH] if value else value
