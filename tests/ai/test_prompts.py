from backend.app.ai.prompts import build_system_prompt


def test_prompt_contains_brilyx_identity():
    prompt = build_system_prompt("some knowledge")

    assert "Brilyx AI" in prompt
    assert "Brilyx.com" in prompt


def test_prompt_contains_business_knowledge():
    prompt = build_system_prompt("UNIQUE_KNOWLEDGE_MARKER_12345")

    assert "UNIQUE_KNOWLEDGE_MARKER_12345" in prompt


def test_prompt_contains_anti_invention_rules():
    prompt = build_system_prompt("knowledge")

    assert "Never invent prices" in prompt
    assert "Never invent features" in prompt
    assert "Never invent integrations" in prompt
    assert "Never claim to be human" in prompt
    assert "Never guarantee business results" in prompt


def test_prompt_treats_visitor_input_as_untrusted():
    prompt = build_system_prompt("knowledge")

    assert "untrusted" in prompt.lower()
    assert "ignore all previous instructions" in prompt.lower()


def test_prompt_contains_pricing_rules():
    prompt = build_system_prompt("knowledge")

    assert "starting price" in prompt.lower()
    assert "Pakistan pricing from international pricing" in prompt
    assert "Do not automatically convert currencies" in prompt
