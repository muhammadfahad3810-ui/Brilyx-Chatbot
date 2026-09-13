"""Phase 4.1 — pricing-tier-vs-service disambiguation.

Covers the exact cases from the Phase 4.1 spec: the deterministic rules
already got these right (confirmed here), so the real Phase 4 bug lived
entirely in response generation — covered separately by the prompt-content
and context-line safeguard tests below.
"""

from backend.app.ai.prompts import BRILYX_CORE_PROMPT, build_context_section
from backend.app.intelligence.models import BusinessType, PackageTier, RequestedService
from backend.app.intelligence.rules import apply_deterministic_rules
from backend.app.intelligence.service import build_context_lines
from backend.app.models import ConversationState


def _fresh_state(**overrides) -> ConversationState:
    defaults = dict(
        conversation_id="conv-1",
        intent="unknown",
        business_type="unknown",
        requested_service="unknown",
        requirements=[],
        demo_requested=False,
        human_handoff_requested=False,
        handoff_recommended=False,
        correction_count=0,
        confidence=0.0,
    )
    defaults.update(overrides)
    return ConversationState(**defaults)


# ---------------------------------------------------------------------------
# A. "Tell me about Business Automation." -> service only, no package implied
# ---------------------------------------------------------------------------


def test_case_a_business_automation_mention_sets_service_not_package():
    fields = apply_deterministic_rules("Tell me about Business Automation.")
    assert fields.requested_service == RequestedService.BUSINESS_AUTOMATION
    assert fields.requested_package is None


# ---------------------------------------------------------------------------
# B. "How much does Business Automation cost?" -> service + pricing intent,
#    but must NOT resolve to any specific package.
# ---------------------------------------------------------------------------


def test_case_b_business_automation_cost_question_does_not_set_a_package():
    fields = apply_deterministic_rules("How much does Business Automation cost?")
    assert fields.requested_service == RequestedService.BUSINESS_AUTOMATION
    assert fields.requested_package is None


def test_case_b_context_includes_safeguard_and_no_package_line():
    state = _fresh_state(requested_service="business_automation", requested_package=None)

    lines = build_context_lines(state)
    section = build_context_section(lines)

    assert any("do not assume or quote a package price" in line.lower() for line in lines)
    assert "asking about package/tier" not in section.lower()
    assert "pkr" not in section.lower()
    assert "$" not in section


# ---------------------------------------------------------------------------
# C. "How much is the Business package?" -> Business tier
# ---------------------------------------------------------------------------


def test_case_c_business_package_question_sets_business_tier():
    fields = apply_deterministic_rules("How much is the Business package?")
    assert fields.requested_package == PackageTier.BUSINESS


# ---------------------------------------------------------------------------
# D. "What is the Business plan?" -> Business tier
# ---------------------------------------------------------------------------


def test_case_d_business_plan_question_sets_business_tier():
    fields = apply_deterministic_rules("What is the Business plan?")
    assert fields.requested_package == PackageTier.BUSINESS


# ---------------------------------------------------------------------------
# E. "Can your Business plan automate my business?" -> both, distinguished
# ---------------------------------------------------------------------------


def test_case_e_business_plan_and_automation_both_identified_distinctly():
    fields = apply_deterministic_rules("Can your Business plan automate my business?")
    assert fields.requested_package == PackageTier.BUSINESS
    assert fields.requested_service == RequestedService.BUSINESS_AUTOMATION


def test_case_e_context_shows_both_service_and_package_without_safeguard_line():
    state = _fresh_state(requested_service="business_automation", requested_package="business")

    lines = build_context_lines(state)

    assert any("requested service: business automation" in line.lower() for line in lines)
    assert any("asking about package/tier: business" in line.lower() for line in lines)
    # The safeguard line only applies when NO package has been identified.
    assert not any("do not assume or quote a package price" in line.lower() for line in lines)


# ---------------------------------------------------------------------------
# F. "I need business automation for my restaurant." -> service set,
#    package remains null, business type captured.
# ---------------------------------------------------------------------------


def test_case_f_business_automation_for_restaurant_leaves_package_null():
    fields = apply_deterministic_rules("I need business automation for my restaurant.")
    assert fields.business_type == BusinessType.RESTAURANT
    assert fields.requested_service == RequestedService.BUSINESS_AUTOMATION
    assert fields.requested_package is None


# ---------------------------------------------------------------------------
# G. "I want the Business package for my restaurant." -> package set
# ---------------------------------------------------------------------------


def test_case_g_business_package_for_restaurant_sets_package():
    fields = apply_deterministic_rules("I want the Business package for my restaurant.")
    assert fields.business_type == BusinessType.RESTAURANT
    assert fields.requested_package == PackageTier.BUSINESS


# ---------------------------------------------------------------------------
# Response-generation prompt: the hard rule must actually be present.
# ---------------------------------------------------------------------------


def test_prompt_contains_explicit_tier_vs_service_disambiguation_rule():
    assert "PRICING TIER VS. SERVICE" in BRILYX_CORE_PROMPT
    assert "NEVER infer a pricing tier merely because a service name" in BRILYX_CORE_PROMPT
    assert "do not quote any package" in BRILYX_CORE_PROMPT.lower() or "do not quote any package's price" in BRILYX_CORE_PROMPT.lower() or "do not quote" in BRILYX_CORE_PROMPT.lower()


def test_prompt_context_note_clarifies_service_does_not_imply_package():
    assert "does not by itself mean any pricing package" in BRILYX_CORE_PROMPT.lower() or "does not by itself mean" in BRILYX_CORE_PROMPT.lower()


def test_no_duplicate_pricing_numbers_introduced_in_prompt_rule_text():
    # The disambiguation rule must describe behavior, not hardcode numbers —
    # knowledge/pricing.md remains the single source of pricing values.
    assert "35,000" not in BRILYX_CORE_PROMPT
    assert "PKR 6,000" not in BRILYX_CORE_PROMPT
    assert "$200" not in BRILYX_CORE_PROMPT
