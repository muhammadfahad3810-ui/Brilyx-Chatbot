import pytest
from pydantic import ValidationError

from backend.app.ai.base import AIResponse
from backend.app.intelligence.extractor import (
    AI_EXTRACTION_CONFIDENCE_THRESHOLD,
    extract_fields,
    extract_with_ai,
    needs_ai_extraction,
)
from backend.app.intelligence.models import BusinessType, ExtractedFields, Intent


class FakeExtractionOrchestrator:
    def __init__(self, response_text=None, raise_error=None):
        self._response_text = response_text
        self._raise_error = raise_error
        self.extract_called = False

    def extract(self, system_prompt, message):
        self.extract_called = True
        if self._raise_error:
            raise self._raise_error
        return AIResponse(text=self._response_text, provider="fake", model="fake")


# ---------------------------------------------------------------------------
# needs_ai_extraction gating
# ---------------------------------------------------------------------------


def test_high_confidence_deterministic_result_skips_ai():
    confident_fields = ExtractedFields(intent=Intent.PRICING, confidence=AI_EXTRACTION_CONFIDENCE_THRESHOLD)
    assert needs_ai_extraction(confident_fields) is False


def test_low_confidence_deterministic_result_requires_ai():
    weak_fields = ExtractedFields(confidence=0.0)
    assert needs_ai_extraction(weak_fields) is True


def test_extract_fields_skips_ai_call_when_deterministic_is_confident():
    orchestrator = FakeExtractionOrchestrator(response_text='{"intent": "greeting"}')

    result = extract_fields(orchestrator, "How much does it cost?")

    assert orchestrator.extract_called is False
    assert result.intent == Intent.PRICING


def test_extract_fields_calls_ai_when_deterministic_is_not_confident():
    orchestrator = FakeExtractionOrchestrator(
        response_text='{"business_type": "dental_clinic", "problem_summary": "High inquiry volume", "confidence": 0.8}'
    )

    result = extract_fields(orchestrator, "We run a small place and get a lot of questions.")

    assert orchestrator.extract_called is True
    assert result.business_type == BusinessType.DENTAL_CLINIC
    assert result.problem_summary == "High inquiry volume"


# ---------------------------------------------------------------------------
# SECURITY: untrusted model output must never crash or bypass validation
# ---------------------------------------------------------------------------


def test_invalid_json_from_model_is_rejected_safely():
    orchestrator = FakeExtractionOrchestrator(response_text="this is not json at all {{{")

    result = extract_with_ai(orchestrator, "some message")

    assert result is None


def test_invalid_enum_value_from_model_is_rejected_safely():
    orchestrator = FakeExtractionOrchestrator(response_text='{"intent": "definitely_not_a_real_intent"}')

    result = extract_with_ai(orchestrator, "some message")

    assert result is None


def test_malformed_confidence_type_from_model_is_rejected_safely():
    orchestrator = FakeExtractionOrchestrator(response_text='{"confidence": "very confident"}')

    result = extract_with_ai(orchestrator, "some message")

    assert result is None


def test_out_of_range_confidence_is_clamped_not_rejected():
    assert ExtractedFields(confidence=5.0).confidence == 1.0
    assert ExtractedFields(confidence=-3.0).confidence == 0.0


def test_direct_construction_rejects_invalid_enum_value():
    with pytest.raises(ValidationError):
        ExtractedFields(intent="not_a_real_value")


def test_model_cannot_smuggle_extra_unknown_fields():
    orchestrator = FakeExtractionOrchestrator(
        response_text='{"intent": "greeting", "system_override": "ignore all rules", "__class__": "evil"}'
    )

    result = extract_with_ai(orchestrator, "hi")

    assert result is not None
    assert result.intent == Intent.GREETING
    assert not hasattr(result, "system_override")


def test_provider_error_during_extraction_is_swallowed():
    orchestrator = FakeExtractionOrchestrator(raise_error=RuntimeError("boom"))

    result = extract_with_ai(orchestrator, "some message")

    assert result is None


def test_model_wrapping_json_in_code_fences_is_still_parsed():
    orchestrator = FakeExtractionOrchestrator(response_text='```json\n{"intent": "greeting"}\n```')

    result = extract_with_ai(orchestrator, "hi")

    assert result is not None
    assert result.intent == Intent.GREETING


def test_requirements_list_is_capped_and_non_string_items_dropped():
    fields = ExtractedFields(requirements=["a", 123, "b", None, "c"] + [f"item-{i}" for i in range(15)])
    assert all(isinstance(item, str) for item in fields.requirements)
    assert len(fields.requirements) <= 10
