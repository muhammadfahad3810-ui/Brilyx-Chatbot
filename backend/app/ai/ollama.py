import httpx

from backend.app.ai.base import AIProvider, AIProviderError, AIResponse, ChatMessage


class OllamaProvider(AIProvider):
    """AI provider backed by a local Ollama server's /api/chat endpoint."""

    def __init__(self, base_url: str, model: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AIResponse:
        payload = {
            "model": self.model,
            "messages": self._build_messages(system_prompt, messages),
            "stream": False,
            # Ollama defaults num_ctx to 2048, which is too small to fit the
            # Brilyx system prompt (identity/rules + full knowledge base)
            # without silently truncating it. Request a window comfortably
            # larger than the prompt so the model actually sees all of it.
            "options": {"temperature": temperature, "num_ctx": 8192},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise AIProviderError("Ollama request timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderError("Could not reach the Ollama server") from exc

        if response.status_code != 200:
            raise AIProviderError(f"Ollama returned an unexpected HTTP status: {response.status_code}")

        try:
            data = response.json()
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AIProviderError("Ollama returned a malformed response") from exc

        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("Ollama returned an empty response")

        return AIResponse(text=text.strip(), provider="ollama", model=self.model)

    @staticmethod
    def _build_messages(system_prompt: str, messages: list[ChatMessage]) -> list[dict]:
        return [{"role": "system", "content": system_prompt}] + [
            {"role": message.role, "content": message.content} for message in messages
        ]
