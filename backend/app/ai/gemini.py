from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from backend.app.ai.base import AIProvider, AIProviderError, AIResponse, ChatMessage

# Gemini's `Content.role` uses "model" for the assistant's own turns, not
# "assistant" (the OpenAI/Ollama convention this codebase's ChatMessage
# already uses elsewhere) — this is the only translation needed between
# the two APIs' message shapes.
_ROLE_MAP = {"user": "user", "assistant": "model"}


class GeminiProvider(AIProvider):
    """AI provider backed by Google's Gemini API (the `google-genai` SDK).

    Mirrors `OllamaProvider` exactly: same `AIProvider` interface, same
    `AIResponse` shape, same "raise `AIProviderError` on any failure"
    contract. This class contains zero Brilyx business logic — it only
    ever forwards the system prompt and conversation history it's given
    and returns the model's raw text. Pricing, lead scoring, lead capture,
    qualification, and every other deterministic rule live entirely
    outside the AI provider layer (see `backend/app/routers/chat.py`) and
    are completely unaffected by which provider is selected.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float | None = None,
        client: "genai.Client | None" = None,
    ):
        if not api_key or not api_key.strip():
            # Fail clearly and immediately (Phase spec requirement) rather
            # than constructing a client that will only fail confusingly
            # on the first real request.
            raise AIProviderError(
                "GEMINI_API_KEY is not configured. Set it in the environment to use AI_PROVIDER=gemini."
            )
        self.model = model
        if client is not None:
            # Test-only seam: production code never passes this — see
            # tests/ai/test_gemini_provider.py. Never used to bypass the
            # api_key check above.
            self._client = client
        else:
            http_options = types.HttpOptions(timeout=int(timeout_seconds * 1000)) if timeout_seconds else None
            self._client = genai.Client(api_key=api_key, http_options=http_options)

    def generate(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AIResponse:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=self._build_contents(messages),
                config=config,
            )
        except genai_errors.APIError as exc:
            # Never include the raw exception text: Gemini error payloads
            # can echo back request details. Report only the category,
            # exactly like OllamaProvider's own HTTP-status-only messages.
            raise AIProviderError(f"Gemini API request failed ({exc.__class__.__name__})") from exc
        except Exception as exc:  # noqa: BLE001 - any transport/SDK failure must be caught, never propagate
            raise AIProviderError(f"Could not reach the Gemini API ({exc.__class__.__name__})") from exc

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("Gemini returned an empty response")

        return AIResponse(text=text.strip(), provider="gemini", model=self.model)

    @staticmethod
    def _build_contents(messages: list[ChatMessage]) -> list[types.Content]:
        return [
            types.Content(
                role=_ROLE_MAP.get(message.role, message.role),
                parts=[types.Part.from_text(text=message.content)],
            )
            for message in messages
        ]
