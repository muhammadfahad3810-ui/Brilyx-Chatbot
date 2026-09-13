from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ChatMessage:
    """A single message in a conversation."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class AIResponse:
    """Normalized response returned by any AI provider."""

    text: str
    provider: str
    model: str


class AIProviderError(Exception):
    """Raised when an AI provider fails in a controlled, expected way.

    Callers (the orchestrator, the API layer) should catch this and turn it
    into a safe, user-facing error response instead of letting the
    underlying provider exception (connection errors, timeouts, HTTP
    errors, malformed responses, ...) propagate.
    """


class AIProvider(ABC):
    """Abstraction that every AI backend (Ollama, and later others) must implement.

    The rest of the application only ever talks to this interface, so a new
    provider can be added without changing the orchestrator or the API.
    """

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        # Default is greedy (0.0): with the full knowledge base in context,
        # even modest sampling temperatures (0.1-0.3) made the small local
        # model inconsistently mix up pricing tiers across repeated calls.
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AIResponse:
        """Generate a response given a system prompt and conversation history.

        Raises:
            AIProviderError: if the provider cannot produce a response for
                any expected reason (unreachable, timeout, bad response,
                malformed payload, etc.).
        """
        raise NotImplementedError
