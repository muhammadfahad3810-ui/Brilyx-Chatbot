"""AIOrchestrator — max_tokens forwarding and extraction-provider routing.

Fully isolated from any real AI provider: fake providers record exactly
what they were called with, so these tests verify the orchestrator's own
wiring (which provider, which max_tokens) rather than any provider's
internals (those are covered by tests/ai/test_*_provider.py).
"""

from backend.app.ai.base import AIResponse, ChatMessage
from backend.app.ai.orchestrator import AIOrchestrator


class FakeProvider:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def generate(self, system_prompt, messages, temperature=0.0, max_tokens=None):
        self.calls.append(
            {"system_prompt": system_prompt, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        )
        return AIResponse(text="ok", provider=self.name, model=self.name)


def test_chat_forwards_configured_chat_max_tokens():
    provider = FakeProvider("main")
    orchestrator = AIOrchestrator(provider=provider, system_prompt="sys", chat_max_tokens=600)

    orchestrator.chat(message="hi")

    assert provider.calls[0]["max_tokens"] == 600


def test_chat_uses_no_max_tokens_by_default_preserving_original_behavior():
    provider = FakeProvider("main")
    orchestrator = AIOrchestrator(provider=provider, system_prompt="sys")

    orchestrator.chat(message="hi")

    assert provider.calls[0]["max_tokens"] is None


def test_extract_uses_the_separate_extraction_provider_when_given():
    main_provider = FakeProvider("main")
    extraction_provider = FakeProvider("extraction")
    orchestrator = AIOrchestrator(
        provider=main_provider,
        system_prompt="sys",
        extraction_provider=extraction_provider,
        extraction_max_tokens=300,
    )

    orchestrator.extract(system_prompt="extract sys", message="hi")

    assert len(main_provider.calls) == 0
    assert len(extraction_provider.calls) == 1
    assert extraction_provider.calls[0]["max_tokens"] == 300
    assert extraction_provider.calls[0]["system_prompt"] == "extract sys"


def test_extract_reuses_main_provider_when_no_extraction_provider_given():
    main_provider = FakeProvider("main")
    orchestrator = AIOrchestrator(provider=main_provider, system_prompt="sys")

    orchestrator.extract(system_prompt="extract sys", message="hi")

    assert len(main_provider.calls) == 1
    assert main_provider.calls[0]["max_tokens"] is None


def test_main_chat_never_goes_through_the_extraction_provider():
    main_provider = FakeProvider("main")
    extraction_provider = FakeProvider("extraction")
    orchestrator = AIOrchestrator(
        provider=main_provider,
        system_prompt="sys",
        extraction_provider=extraction_provider,
        chat_max_tokens=600,
        extraction_max_tokens=300,
    )

    orchestrator.chat(message="hi")

    assert len(main_provider.calls) == 1
    assert len(extraction_provider.calls) == 0
    assert main_provider.calls[0]["max_tokens"] == 600


def test_chat_history_and_context_still_forwarded_correctly_alongside_max_tokens():
    provider = FakeProvider("main")
    orchestrator = AIOrchestrator(provider=provider, system_prompt="sys", chat_max_tokens=600)
    history = [ChatMessage(role="user", content="earlier"), ChatMessage(role="assistant", content="reply")]

    orchestrator.chat(message="hi", history=history, context="extra context")

    call = provider.calls[0]
    assert call["system_prompt"] == "sys\n\nextra context"
    assert [m.content for m in call["messages"]] == ["earlier", "reply", "hi"]
    assert call["max_tokens"] == 600
