import json

from pydantic import ValidationError

from backend.app.ai.orchestrator import AIOrchestrator
from backend.app.intelligence.models import (
    BusinessType,
    Channel,
    ExtractedFields,
    Intent,
    PackageTier,
    RequestedService,
)
from backend.app.intelligence.rules import apply_deterministic_rules

# Below this confidence, deterministic rules haven't confidently captured
# what this turn is about, so an AI extraction pass is used to supplement
# understanding (see docs/conversation-intelligence.md for the rationale
# and worked examples).
AI_EXTRACTION_CONFIDENCE_THRESHOLD = 0.85

_EXTRACTION_PROMPT_TEMPLATE = """\
You are a silent data-extraction component. You are NOT talking to the
visitor and must NOT write a conversational reply of any kind.

Read the single visitor message below and output ONLY one JSON object (no
markdown, no code fences, no commentary, no explanation) with exactly these
fields:

{{
  "intent": one of [{intents}] or null,
  "business_type": one of [{business_types}] or null,
  "business_name": short string or null,
  "country": short string or null,
  "currency": "PKR" or "USD" or null,
  "current_channel": one of [{channels}] or null,
  "requested_service": one of [{services}] or null,
  "requested_package": one of [{packages}] or null,
  "problem_summary": short string (max ~15 words) or null,
  "requirements": array of short strings (max 5 items, [] if none),
  "demo_requested": true or false,
  "human_handoff_requested": true or false,
  "confidence": number between 0.0 and 1.0
}}

Rules:
- Only extract what the message actually states. Never invent facts.
- Use null (or false/[] for booleans/arrays) for anything not stated.
- Enum fields must be exactly one of the listed values, or null. Never invent a new category.
- If the business type isn't one of the listed specific categories but IS a business, use "other_business".
- Output ONLY the JSON object and nothing else.

Visitor message:
{message}
"""


def _build_extraction_prompt(message: str) -> str:
    return _EXTRACTION_PROMPT_TEMPLATE.format(
        intents=", ".join(f'"{i.value}"' for i in Intent),
        business_types=", ".join(f'"{b.value}"' for b in BusinessType),
        channels=", ".join(f'"{c.value}"' for c in Channel),
        services=", ".join(f'"{s.value}"' for s in RequestedService),
        packages=", ".join(f'"{p.value}"' for p in PackageTier),
        message=message,
    )


def needs_ai_extraction(deterministic_fields: ExtractedFields) -> bool:
    """Whether deterministic rules were confident enough to skip an AI call."""
    return deterministic_fields.confidence < AI_EXTRACTION_CONFIDENCE_THRESHOLD


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def extract_with_ai(orchestrator: AIOrchestrator, message: str) -> ExtractedFields | None:
    """Best-effort AI extraction. Never raises — any failure returns None.

    This is intentionally defensive: extraction is supplementary
    intelligence, not part of the core chat contract, so a timeout,
    malformed JSON, or an invalid enum value from the model must never
    break message delivery. Untrusted model output is parsed and then fully
    validated through ExtractedFields (Pydantic) before it is trusted at all.
    """
    try:
        response = orchestrator.extract(system_prompt=_build_extraction_prompt(message), message=message)
        raw = _strip_code_fences(response.text)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return ExtractedFields(**data)
    except (ValidationError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return None
    except Exception:
        # Defense in depth: extraction must never take down the chat flow,
        # regardless of failure mode (provider error, timeout, etc.).
        return None


def extract_fields(orchestrator: AIOrchestrator, message: str) -> ExtractedFields:
    """Run the full extraction pass for one visitor message.

    Deterministic rules always run first and are used as-is when confident.
    An AI pass only runs, and only supplements (never overrides) the
    deterministic result, when the rules weren't confident enough.
    """
    deterministic = apply_deterministic_rules(message)
    if not needs_ai_extraction(deterministic):
        return deterministic

    ai_fields = extract_with_ai(orchestrator, message)
    if ai_fields is None:
        return deterministic

    merged = deterministic.model_dump()
    for field_name, value in ai_fields.model_dump().items():
        current = merged.get(field_name)
        if field_name == "requirements":
            combined = list(current or [])
            for item in value:
                if item not in combined:
                    combined.append(item)
            merged[field_name] = combined[:10]
        elif field_name in ("demo_requested", "human_handoff_requested"):
            merged[field_name] = bool(current) or bool(value)
        elif field_name == "confidence":
            merged[field_name] = max(current or 0.0, value or 0.0)
        elif current in (None, False, [], "") and value not in (None, False, [], ""):
            merged[field_name] = value

    return ExtractedFields(**merged)
