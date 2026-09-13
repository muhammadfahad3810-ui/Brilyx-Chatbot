import groq

from backend.app.ai.base import AIProvider, AIProviderError, AIResponse, ChatMessage

# Model choice: inspected the models actually offered by the installed
# `groq` SDK version (its `chat.completions.create(model=...)` type hint
# lists them) rather than guessing. Deliberately NOT using:
#   - "compound-beta" / "compound-beta-mini" — Groq's agentic models,
#     which can autonomously browse the web / call tools. Brilyx AI must
#     only ever answer from the approved knowledge base assembled by this
#     application (see backend/app/ai/prompts.py) — letting the model
#     independently fetch outside information would violate the
#     "never invent/fetch facts" rule this project enforces everywhere
#     else.
#   - "meta-llama/llama-guard-4-12b" — a content-moderation/safety
#     classifier, not a conversational model.
# "llama-3.3-70b-versatile" (Settings.GROQ_MODEL's default, set in
# backend/app/config.py) is a general-purpose, instruction-following
# production model suitable for a business chatbot, fully overridable via
# GROQ_MODEL without any code change.


class GroqProvider(AIProvider):
    """AI provider backed by Groq's OpenAI-compatible chat completions API.

    Mirrors `OllamaProvider`/`GeminiProvider` exactly: same `AIProvider`
    interface, same `AIResponse` shape, same "raise `AIProviderError` on
    any failure" contract. This class contains zero Brilyx business
    logic — it only ever forwards the system prompt and conversation
    history it's given and returns the model's raw text. Pricing, lead
    scoring, lead capture, qualification, and every other deterministic
    rule live entirely outside the AI provider layer (see
    backend/app/routers/chat.py) and are completely unaffected by which
    provider is selected.

    Groq's chat completions API is OpenAI-compatible, using the same
    `{"role": "system"|"user"|"assistant", "content": ...}` message shape
    this codebase's `ChatMessage`/`OllamaProvider` already use — unlike
    Gemini, no role translation is needed here.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float | None = None,
        client: "groq.Groq | None" = None,
    ):
        if not api_key or not api_key.strip():
            # Fail clearly and immediately rather than constructing a
            # client that will only fail confusingly on the first real
            # request.
            raise AIProviderError(
                "GROQ_API_KEY is not configured. Set it in the environment to use AI_PROVIDER=groq."
            )
        self.model = model
        if client is not None:
            # Test-only seam: production code never passes this — see
            # tests/ai/test_groq_provider.py. Never used to bypass the
            # api_key check above.
            self._client = client
        else:
            # The installed `groq` SDK treats an explicit `timeout=None` as
            # "disable timeouts entirely" (it only falls back to its own
            # default when the argument is omitted). So when no timeout is
            # configured, the keyword must be left out entirely — not
            # passed as `None` — to keep the SDK's own default timeout
            # active rather than silently allowing requests to hang
            # forever.
            client_kwargs = {"api_key": api_key}
            if timeout_seconds is not None:
                client_kwargs["timeout"] = timeout_seconds
            self._client = groq.Groq(**client_kwargs)

    def generate(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AIResponse:
        kwargs = {
            "model": self.model,
            "messages": self._build_messages(system_prompt, messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        try:
            response = self._client.chat.completions.create(**kwargs)
        except groq.GroqError as exc:
            # Never include the raw exception text: Groq error payloads
            # can echo back request details. Report only the category,
            # exactly like OllamaProvider's/GeminiProvider's own
            # category-only error messages.
            raise AIProviderError(f"Groq API request failed ({exc.__class__.__name__})") from exc
        except Exception as exc:  # noqa: BLE001 - any transport/SDK failure must be caught, never propagate
            raise AIProviderError(f"Could not reach the Groq API ({exc.__class__.__name__})") from exc

        try:
            text = response.choices[0].message.content
        except (IndexError, AttributeError, TypeError) as exc:
            raise AIProviderError("Groq returned a malformed response") from exc

        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("Groq returned an empty response")

        return AIResponse(text=text.strip(), provider="groq", model=self.model)

    @staticmethod
    def _build_messages(system_prompt: str, messages: list[ChatMessage]) -> list[dict]:
        return [{"role": "system", "content": system_prompt}] + [
            {"role": message.role, "content": message.content} for message in messages
        ]
