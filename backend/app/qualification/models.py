from enum import Enum

from pydantic import BaseModel, ConfigDict


class QualificationLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LeadQualificationResult(BaseModel):
    """Pure, deterministic scoring result for a single conversation snapshot.

    Always the return value of `qualification.rules.calculate_qualification`
    — never parsed from a client request or model output, and never
    mutated after construction (see docs/lead-qualification.md).
    """

    model_config = ConfigDict(frozen=True)

    score: int
    level: QualificationLevel
    reasons: list[str]
