from functools import lru_cache

from backend.app.ai.base import AIProvider, AIProviderError, AIResponse, ChatMessage
from backend.app.ai.knowledge import load_knowledge_base
from backend.app.ai.ollama import OllamaProvider
from backend.app.ai.prompts import build_system_prompt
from backend.app.config import Settings, settings


def build_provider(config: Settings = settings) -> AIProvider:
    """Select and construct the configured AI provider.

    Adding a new provider means adding a branch here (and a new provider
    class) — the orchestrator and API never need to change.
    """
    if config.AI_PROVIDER == "ollama":
        return OllamaProvider(
            base_url=config.OLLAMA_BASE_URL,
            model=config.OLLAMA_MODEL,
            timeout_seconds=config.OLLAMA_TIMEOUT_SECONDS,
        )
    raise AIProviderError(f"Unsupported AI_PROVIDER: {config.AI_PROVIDER!r}")


class AIOrchestrator:
    """Coordinates the system prompt, business knowledge, and AI provider.

    This is the only piece of the application that knows both "how to build
    a Brilyx prompt" and "how to call an AI provider". It does not contain
    lead scoring, CRM, or any sales-process logic.
    """

    def __init__(self, provider: AIProvider, system_prompt: str):
        self._provider = provider
        self._system_prompt = system_prompt

    def chat(
        self,
        message: str,
        history: list[ChatMessage] | None = None,
        context: str | None = None,
    ) -> AIResponse:
        """Generate a Brilyx sales/support reply.

        `context` (if given) is appended to the cached system prompt for
        this call only — it never mutates the shared, cached base prompt —
        so per-conversation state can inform a reply without rebuilding the
        knowledge base on every turn.
        """
        system_prompt = self._system_prompt if context is None else f"{self._system_prompt}\n\n{context}"
        messages = [*(history or []), ChatMessage(role="user", content=message)]
        return self._provider.generate(system_prompt=system_prompt, messages=messages)

    def extract(self, system_prompt: str, message: str) -> AIResponse:
        """Run a one-off, non-conversational call against the same provider.

        Used by the conversation-intelligence layer for structured-JSON
        extraction. This deliberately bypasses the cached Brilyx sales
        system prompt and knowledge base — extraction doesn't need Brilyx
        facts, only the visitor's message — while still reusing the same
        configured provider/connection.
        """
        return self._provider.generate(system_prompt=system_prompt, messages=[ChatMessage(role="user", content=message)])


@lru_cache
def get_orchestrator() -> AIOrchestrator:
    """Build the process-wide orchestrator once (knowledge + prompt + provider)."""
    knowledge = load_knowledge_base()
    system_prompt = build_system_prompt(knowledge)
    provider = build_provider()
    return AIOrchestrator(provider=provider, system_prompt=system_prompt)
