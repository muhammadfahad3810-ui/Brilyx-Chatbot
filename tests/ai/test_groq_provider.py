"""GroqProvider — fully mocked, no real API key and no network access.

A fake `groq.Groq` client is injected directly into the provider (the
same seam production code never uses), so these tests exercise the
actual request-building/response-parsing/error-handling logic without
depending on the real `groq` SDK making any HTTP call.
"""

from unittest.mock import patch

import pytest
from groq import APITimeoutError as GroqAPITimeoutError
from groq import AuthenticationError as GroqAuthenticationError
from groq import RateLimitError as GroqRateLimitError

from backend.app.ai import groq as groq_provider_module
from backend.app.ai.base import AIProviderError, ChatMessage
from backend.app.ai.groq import GroqProvider


class FakeMessage:
    def __init__(self, content=None):
        self.content = content


class FakeChoice:
    def __init__(self, content=None):
        self.message = FakeMessage(content)


class FakeChatCompletion:
    def __init__(self, content=None, choices=None):
        self.choices = choices if choices is not None else [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        if self._error:
            raise self._error
        return self._response


class FakeChat:
    def __init__(self, completions: FakeCompletions):
        self.completions = completions


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = FakeChat(completions)


def _make_fake_response(*, status_code=401, body=None):
    """Build a minimal fake httpx.Response-like object for SDK error constructors."""
    import httpx

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return httpx.Response(status_code=status_code, request=request, json=body or {})


def _provider(completions: FakeCompletions, model="llama-3.3-70b-versatile", api_key="test-key"):
    return GroqProvider(api_key=api_key, model=model, client=FakeClient(completions))


# ---------------------------------------------------------------------------
# Initialization / missing / invalid API key
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_ai_provider_error_clearly():
    with pytest.raises(AIProviderError) as exc_info:
        GroqProvider(api_key="", model="llama-3.3-70b-versatile")
    assert "GROQ_API_KEY" in str(exc_info.value)


def test_whitespace_only_api_key_is_treated_as_missing():
    with pytest.raises(AIProviderError):
        GroqProvider(api_key="   ", model="llama-3.3-70b-versatile")


def test_none_like_empty_key_variants_all_fail_clearly():
    for bogus_key in ["", "   ", "\t", "\n"]:
        with pytest.raises(AIProviderError):
            GroqProvider(api_key=bogus_key, model="llama-3.3-70b-versatile")


def test_provider_initializes_with_a_non_empty_key_and_injected_client():
    provider = _provider(FakeCompletions(response=FakeChatCompletion(content="ok")))
    assert provider.model == "llama-3.3-70b-versatile"


def test_never_constructs_a_real_client_when_a_fake_one_is_injected():
    completions = FakeCompletions(response=FakeChatCompletion(content="ok"))
    provider = _provider(completions)
    provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])
    assert completions.last_call is not None  # the fake was actually used


# ---------------------------------------------------------------------------
# Timeout forwarding (real `groq.Groq` constructor, no injected client)
# ---------------------------------------------------------------------------


def test_numeric_timeout_is_passed_through_unchanged_to_the_groq_client():
    with patch.object(groq_provider_module.groq, "Groq") as mock_groq_ctor:
        GroqProvider(api_key="test-key", model="llama-3.3-70b-versatile", timeout_seconds=37.5)

    mock_groq_ctor.assert_called_once_with(api_key="test-key", timeout=37.5)


def test_none_timeout_omits_the_timeout_keyword_entirely_so_the_sdk_default_applies():
    with patch.object(groq_provider_module.groq, "Groq") as mock_groq_ctor:
        GroqProvider(api_key="test-key", model="llama-3.3-70b-versatile", timeout_seconds=None)

    mock_groq_ctor.assert_called_once_with(api_key="test-key")
    assert "timeout" not in mock_groq_ctor.call_args.kwargs


def test_unspecified_timeout_also_omits_the_timeout_keyword():
    with patch.object(groq_provider_module.groq, "Groq") as mock_groq_ctor:
        GroqProvider(api_key="test-key", model="llama-3.3-70b-versatile")

    assert "timeout" not in mock_groq_ctor.call_args.kwargs


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


def test_configured_model_is_used_in_the_request_and_the_response():
    completions = FakeCompletions(response=FakeChatCompletion(content="Hello!"))
    provider = _provider(completions, model="llama-3.1-8b-instant")

    result = provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert completions.last_call["model"] == "llama-3.1-8b-instant"
    assert result.model == "llama-3.1-8b-instant"
    assert result.provider == "groq"


# ---------------------------------------------------------------------------
# Successful response / request construction
# ---------------------------------------------------------------------------


def test_successful_response_is_returned_normalized():
    completions = FakeCompletions(response=FakeChatCompletion(content="  Hello from Groq!  "))
    provider = _provider(completions)

    result = provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert result.text == "Hello from Groq!"
    assert result.provider == "groq"


def test_system_prompt_is_preserved_as_the_first_system_message():
    completions = FakeCompletions(response=FakeChatCompletion(content="ok"))
    provider = _provider(completions)

    provider.generate(system_prompt="THE FULL BRILYX SYSTEM PROMPT", messages=[ChatMessage(role="user", content="hi")])

    sent_messages = completions.last_call["messages"]
    assert sent_messages[0] == {"role": "system", "content": "THE FULL BRILYX SYSTEM PROMPT"}


def test_conversation_history_is_preserved_in_order_with_no_role_translation():
    completions = FakeCompletions(response=FakeChatCompletion(content="ok"))
    provider = _provider(completions)

    history = [
        ChatMessage(role="user", content="First message"),
        ChatMessage(role="assistant", content="First reply"),
        ChatMessage(role="user", content="Second message"),
    ]
    provider.generate(system_prompt="sys", messages=history)

    sent_messages = completions.last_call["messages"]
    # [0] is the injected system message; history follows unchanged —
    # Groq's OpenAI-compatible API uses "assistant" directly, unlike
    # Gemini's "model" role, so no translation should occur.
    assert sent_messages[1:] == [
        {"role": "user", "content": "First message"},
        {"role": "assistant", "content": "First reply"},
        {"role": "user", "content": "Second message"},
    ]


def test_temperature_is_forwarded():
    completions = FakeCompletions(response=FakeChatCompletion(content="ok"))
    provider = _provider(completions)

    provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")], temperature=0.2)

    assert completions.last_call["temperature"] == 0.2


def test_max_tokens_is_forwarded_when_provided():
    completions = FakeCompletions(response=FakeChatCompletion(content="ok"))
    provider = _provider(completions)

    provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")], max_tokens=256)

    assert completions.last_call["max_tokens"] == 256


def test_max_tokens_is_omitted_entirely_when_not_provided():
    completions = FakeCompletions(response=FakeChatCompletion(content="ok"))
    provider = _provider(completions)

    provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert "max_tokens" not in completions.last_call


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_authentication_error_raises_ai_provider_error():
    error = GroqAuthenticationError(
        "invalid api key", response=_make_fake_response(status_code=401), body={"error": "invalid api key"}
    )
    completions = FakeCompletions(error=error)
    provider = _provider(completions)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_timeout_error_raises_ai_provider_error():
    import httpx

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = GroqAPITimeoutError(request=request)
    completions = FakeCompletions(error=error)
    secret_key = "gsk_FAKE_DO_NOT_USE_timeout_secret"
    provider = _provider(completions, api_key=secret_key)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(system_prompt="THE FULL BRILYX SYSTEM PROMPT", messages=[ChatMessage(role="user", content="hi")])

    message = str(exc_info.value)
    assert "APITimeoutError" in message  # category-only message, matching the other GroqError branches
    assert secret_key not in message
    assert "THE FULL BRILYX SYSTEM PROMPT" not in message
    assert "hi" not in message


def test_rate_limit_error_raises_ai_provider_error():
    error = GroqRateLimitError(
        "rate limited", response=_make_fake_response(status_code=429), body={"error": "rate limited"}
    )
    completions = FakeCompletions(error=error)
    provider = _provider(completions)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_unexpected_exception_is_also_converted_to_ai_provider_error():
    completions = FakeCompletions(error=RuntimeError("unexpected transport failure"))
    provider = _provider(completions)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_empty_response_text_raises_ai_provider_error():
    completions = FakeCompletions(response=FakeChatCompletion(content="   "))
    provider = _provider(completions)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_none_response_text_raises_ai_provider_error():
    completions = FakeCompletions(response=FakeChatCompletion(content=None))
    provider = _provider(completions)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_no_choices_in_response_raises_ai_provider_error():
    completions = FakeCompletions(response=FakeChatCompletion(choices=[]))
    provider = _provider(completions)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# Credential safety
# ---------------------------------------------------------------------------


def test_api_key_never_appears_in_a_raised_error_message():
    secret_key = "gsk_FAKE_DO_NOT_USE_super_secret_value"
    error = GroqAuthenticationError(
        f"invalid key {secret_key}", response=_make_fake_response(status_code=401), body={"error": secret_key}
    )
    completions = FakeCompletions(error=error)
    provider = _provider(completions, api_key=secret_key)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert secret_key not in str(exc_info.value)


def test_api_key_never_appears_in_error_message_when_using_the_extraction_model():
    # Same guarantee as above, but using a different (non-default) model —
    # confirms credential safety doesn't depend on which model/provider
    # instance (main chat vs. extraction) raised the error.
    secret_key = "gsk_FAKE_DO_NOT_USE_extraction_secret"
    error = GroqAuthenticationError(
        f"invalid key {secret_key}", response=_make_fake_response(status_code=401), body={"error": secret_key}
    )
    completions = FakeCompletions(error=error)
    provider = _provider(completions, model="openai/gpt-oss-20b", api_key=secret_key)

    with pytest.raises(AIProviderError) as exc_info:
        provider.generate(system_prompt="extraction sys", messages=[ChatMessage(role="user", content="hi")], max_tokens=300)

    assert secret_key not in str(exc_info.value)


def test_missing_key_error_message_does_not_echo_a_key_value():
    with pytest.raises(AIProviderError) as exc_info:
        GroqProvider(api_key="", model="llama-3.3-70b-versatile")
    assert "gsk_" not in str(exc_info.value)
