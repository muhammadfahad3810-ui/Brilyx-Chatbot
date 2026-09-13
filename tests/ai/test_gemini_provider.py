"""GeminiProvider — fully mocked, no real API key and no network access.

A fake `genai.Client` is injected directly into the provider (the same
seam production code never uses), so these tests exercise the actual
request-building/response-parsing/error-handling logic without depending
on the real `google-genai` SDK making any HTTP call.
"""

import pytest
from google.genai import errors as genai_errors

from backend.app.ai.base import AIProviderError, ChatMessage
from backend.app.ai.gemini import GeminiProvider


class FakeGeminiResponse:
    def __init__(self, text=None):
        self.text = text


class FakeModels:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_call = None

    def generate_content(self, *, model, contents, config):
        self.last_call = {"model": model, "contents": contents, "config": config}
        if self._error:
            raise self._error
        return self._response


class FakeClient:
    def __init__(self, models: FakeModels):
        self.models = models


def _provider(models: FakeModels, model="gemini-2.0-flash", api_key="test-key"):
    return GeminiProvider(api_key=api_key, model=model, client=FakeClient(models))


# ---------------------------------------------------------------------------
# Initialization / missing API key
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_ai_provider_error_clearly():
    with pytest.raises(AIProviderError) as exc_info:
        GeminiProvider(api_key="", model="gemini-2.0-flash")
    assert "GEMINI_API_KEY" in str(exc_info.value)


def test_whitespace_only_api_key_is_treated_as_missing():
    with pytest.raises(AIProviderError):
        GeminiProvider(api_key="   ", model="gemini-2.0-flash")


def test_provider_initializes_with_a_non_empty_key_and_injected_client():
    provider = _provider(FakeModels(response=FakeGeminiResponse(text="ok")))
    assert provider.model == "gemini-2.0-flash"


def test_never_constructs_a_real_client_when_a_fake_one_is_injected():
    # If this test ever tried to reach the network, it would hang/fail in
    # a sandboxed test environment — its passing at all is part of the
    # proof that no real request is made.
    models = FakeModels(response=FakeGeminiResponse(text="ok"))
    provider = _provider(models)
    provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])
    assert models.last_call is not None  # the fake was actually used


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


def test_configured_model_is_used_in_the_request_and_the_response():
    models = FakeModels(response=FakeGeminiResponse(text="Hello!"))
    provider = _provider(models, model="gemini-1.5-pro")

    result = provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert models.last_call["model"] == "gemini-1.5-pro"
    assert result.model == "gemini-1.5-pro"
    assert result.provider == "gemini"


# ---------------------------------------------------------------------------
# Successful response / request construction
# ---------------------------------------------------------------------------


def test_successful_response_is_returned_normalized():
    models = FakeModels(response=FakeGeminiResponse(text="  Hello from Gemini!  "))
    provider = _provider(models)

    result = provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert result.text == "Hello from Gemini!"
    assert result.provider == "gemini"


def test_system_prompt_is_preserved_and_passed_as_system_instruction():
    models = FakeModels(response=FakeGeminiResponse(text="ok"))
    provider = _provider(models)

    provider.generate(system_prompt="THE FULL BRILYX SYSTEM PROMPT", messages=[ChatMessage(role="user", content="hi")])

    assert models.last_call["config"].system_instruction == "THE FULL BRILYX SYSTEM PROMPT"


def test_conversation_history_is_preserved_in_order_with_roles_translated():
    models = FakeModels(response=FakeGeminiResponse(text="ok"))
    provider = _provider(models)

    history = [
        ChatMessage(role="user", content="First message"),
        ChatMessage(role="assistant", content="First reply"),
        ChatMessage(role="user", content="Second message"),
    ]
    provider.generate(system_prompt="sys", messages=history)

    contents = models.last_call["contents"]
    assert len(contents) == 3
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "First message"
    # Gemini uses "model" for the assistant's own turns, not "assistant".
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "First reply"
    assert contents[2].role == "user"
    assert contents[2].parts[0].text == "Second message"


def test_temperature_and_max_tokens_are_forwarded():
    models = FakeModels(response=FakeGeminiResponse(text="ok"))
    provider = _provider(models)

    provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=256)

    config = models.last_call["config"]
    assert config.temperature == 0.2
    assert config.max_output_tokens == 256


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_api_error_raises_ai_provider_error():
    error = genai_errors.ClientError(429, {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}})
    models = FakeModels(error=error)
    provider = _provider(models)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_unexpected_exception_is_also_converted_to_ai_provider_error():
    models = FakeModels(error=RuntimeError("unexpected transport failure"))
    provider = _provider(models)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_empty_response_text_raises_ai_provider_error():
    models = FakeModels(response=FakeGeminiResponse(text="   "))
    provider = _provider(models)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_none_response_text_raises_ai_provider_error():
    models = FakeModels(response=FakeGeminiResponse(text=None))
    provider = _provider(models)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# Credential safety
# ---------------------------------------------------------------------------


def test_api_key_never_appears_in_a_raised_error_message():
    secret_key = "AIzaSy-FAKE-DO-NOT-USE-super-secret-value"
    error = genai_errors.ClientError(401, {"error": {"message": f"invalid key {secret_key}", "status": "UNAUTHENTICATED"}})
    models = FakeModels(error=error)
    provider = _provider(models, api_key=secret_key)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert secret_key not in str(exc_info.value)


def test_missing_key_error_message_does_not_echo_a_key_value():
    # Even the "missing key" error must never accidentally echo back
    # something that looks like a key (e.g. if a caller passed whitespace).
    with pytest.raises(AIProviderError) as exc_info:
        GeminiProvider(api_key="", model="gemini-2.0-flash")
    assert "AIza" not in str(exc_info.value)
