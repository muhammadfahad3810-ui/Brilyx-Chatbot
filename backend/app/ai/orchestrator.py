from functools import lru_cache

from backend.app.ai.base import AIProvider, AIProviderError, AIResponse, ChatMessage
from backend.app.ai.gemini import GeminiProvider
from backend.app.ai.groq import GroqProvider
from backend.app.ai.knowledge import load_knowledge_base
from backend.app.ai.ollama import OllamaProvider
from backend.app.ai.prompts import build_system_prompt
from backend.app.config import Settings, settings


def build_provider(config: Settings = settings) -> AIProvider:
    """Select and construct the configured AI provider.

    Adding a new provider means adding a branch here (and a new provider
    class) — the orchestrator and API never need to change. Switching
    `AI_PROVIDER` never touches business logic: pricing, qualification,
    lead capture, and every deterministic rule live entirely outside this
    layer (see backend/app/routers/chat.py).
    """
    if config.AI_PROVIDER == "ollama":
        return OllamaProvider(
            base_url=config.OLLAMA_BASE_URL,
            model=config.OLLAMA_MODEL,
            timeout_seconds=config.OLLAMA_TIMEOUT_SECONDS,
        )
    if config.AI_PROVIDER == "gemini":
        return GeminiProvider(
            api_key=config.GEMINI_API_KEY,
            model=config.GEMINI_MODEL,
            timeout_seconds=config.GEMINI_TIMEOUT_SECONDS,
        )
    if config.AI_PROVIDER == "groq":
        return GroqProvider(
            api_key=config.GROQ_API_KEY,
            model=config.GROQ_MODEL,
            timeout_seconds=config.GROQ_TIMEOUT_SECONDS,
        )
    raise AIProviderError(f"Unsupported AI_PROVIDER: {config.AI_PROVIDER!r}")


def build_extraction_provider(config: Settings = settings) -> AIProvider:
    """Provider used for the structured lead-extraction call.

    Reused as-is from `build_provider()` for every AI_PROVIDER except
    "groq": Ollama and Gemini keep behaving exactly as before (a single
    shared provider for both chat and extraction). For "groq" specifically,
    extraction is routed through a separate, smaller `GroqProvider`
    (`GROQ_EXTRACTION_MODEL`) instead of `GROQ_MODEL` — Groq enforces
    tokens-per-minute limits per model, so this keeps extraction traffic
    from competing with the main chat call's TPM budget. This never
    changes what extraction receives (same system prompt, same message,
    same Pydantic validation/deterministic fallback in
    backend/app/intelligence/extractor.py) — only which model processes it.
    """
    if config.AI_PROVIDER == "groq":
        return GroqProvider(
            api_key=config.GROQ_API_KEY,
            model=config.GROQ_EXTRACTION_MODEL,
            timeout_seconds=config.GROQ_TIMEOUT_SECONDS,
        )
    return build_provider(config)


class AIOrchestrator:
    """Coordinates the system prompt, business knowledge, and AI provider.

    This is the only piece of the application that knows both "how to build
    a Brilyx prompt" and "how to call an AI provider". It does not contain
    lead scoring, CRM, or any sales-process logic.
    """

    def __init__(
        self,
        provider: AIProvider,
        system_prompt: str,
        extraction_provider: AIProvider | None = None,
        chat_max_tokens: int | None = None,
        extraction_max_tokens: int | None = None,
    ):
        self._provider = provider
        self._system_prompt = system_prompt
        # Defaults preserve the original behavior exactly: reuse the main
        # provider for extraction, and never cap output, unless the caller
        # (see get_orchestrator below) explicitly opts in.
        self._extraction_provider = extraction_provider or provider
        self._chat_max_tokens = chat_max_tokens
        self._extraction_max_tokens = extraction_max_tokens

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
        return self._provider.generate(system_prompt=system_prompt, messages=messages, max_tokens=self._chat_max_tokens)

    def extract(self, system_prompt: str, message: str) -> AIResponse:
        """Run a one-off, non-conversational structured-extraction call.

        Used by the conversation-intelligence layer for structured-JSON
        extraction. This deliberately bypasses the cached Brilyx sales
        system prompt and knowledge base — extraction doesn't need Brilyx
        facts, only the visitor's message. Uses `_extraction_provider`,
        which is a separate (typically smaller/cheaper) provider only when
        one was configured (see build_extraction_provider) — otherwise the
        same provider/connection as `chat()`.
        """
        return self._extraction_provider.generate(
            system_prompt=system_prompt,
            messages=[ChatMessage(role="user", content=message)],
            max_tokens=self._extraction_max_tokens,
        )


@lru_cache
def get_orchestrator() -> AIOrchestrator:
    """Build the process-wide orchestrator once (knowledge + prompt + provider)."""
    knowledge = load_knowledge_base()
    system_prompt = build_system_prompt(knowledge)
    provider = build_provider()
    extraction_provider = build_extraction_provider()
    # max_tokens caps are only meaningful for Groq today (see
    # Settings.GROQ_MAX_TOKENS/GROQ_EXTRACTION_MAX_TOKENS) — Ollama and
    # Gemini keep their original, uncapped behavior exactly as before.
    chat_max_tokens = settings.GROQ_MAX_TOKENS if settings.AI_PROVIDER == "groq" else None
    extraction_max_tokens = settings.GROQ_EXTRACTION_MAX_TOKENS if settings.AI_PROVIDER == "groq" else None
    return AIOrchestrator(
        provider=provider,
        system_prompt=system_prompt,
        extraction_provider=extraction_provider,
        chat_max_tokens=chat_max_tokens,
        extraction_max_tokens=extraction_max_tokens,
    )
