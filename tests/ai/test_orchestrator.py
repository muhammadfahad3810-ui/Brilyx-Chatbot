"""backend.app.ai.orchestrator.build_provider — provider selection.

Deterministic, no network access: GeminiProvider/GroqProvider construction
here only ever exercises the "missing/present API key" and "which class
was built" logic — no real Gemini or Groq request is made anywhere in
this file.
"""

import pytest

from backend.app.ai.base import AIProviderError
from backend.app.ai.gemini import GeminiProvider
from backend.app.ai.groq import GroqProvider
from backend.app.ai.ollama import OllamaProvider
from backend.app.ai.orchestrator import build_extraction_provider, build_provider
from backend.app.config import Settings


def _settings(**overrides):
    defaults = dict(_env_file=None)
    defaults.update(overrides)
    return Settings(**defaults)


def test_ollama_is_selected_by_default():
    provider = build_provider(_settings())
    assert isinstance(provider, OllamaProvider)


def test_ollama_provider_uses_configured_base_url_and_model():
    provider = build_provider(_settings(OLLAMA_BASE_URL="http://custom-ollama-host:11434", OLLAMA_MODEL="custom-model"))
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://custom-ollama-host:11434"
    assert provider.model == "custom-model"


def test_gemini_is_selected_when_ai_provider_is_gemini():
    provider = build_provider(_settings(AI_PROVIDER="gemini", GEMINI_API_KEY="test-key"))
    assert isinstance(provider, GeminiProvider)


def test_gemini_provider_uses_configured_model():
    provider = build_provider(_settings(AI_PROVIDER="gemini", GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-1.5-pro"))
    assert provider.model == "gemini-1.5-pro"


def test_gemini_without_api_key_fails_clearly_at_selection_time():
    with pytest.raises(AIProviderError) as exc_info:
        build_provider(_settings(AI_PROVIDER="gemini", GEMINI_API_KEY=""))
    assert "GEMINI_API_KEY" in str(exc_info.value)


def test_groq_is_selected_when_ai_provider_is_groq():
    provider = build_provider(_settings(AI_PROVIDER="groq", GROQ_API_KEY="test-key"))
    assert isinstance(provider, GroqProvider)


def test_groq_provider_uses_configured_model():
    provider = build_provider(_settings(AI_PROVIDER="groq", GROQ_API_KEY="test-key", GROQ_MODEL="llama-3.1-8b-instant"))
    assert provider.model == "llama-3.1-8b-instant"


def test_groq_without_api_key_fails_clearly_at_selection_time():
    with pytest.raises(AIProviderError) as exc_info:
        build_provider(_settings(AI_PROVIDER="groq", GROQ_API_KEY=""))
    assert "GROQ_API_KEY" in str(exc_info.value)


def test_unsupported_provider_raises_ai_provider_error():
    with pytest.raises(AIProviderError):
        build_provider(_settings(AI_PROVIDER="some-unknown-provider"))


def test_default_gemini_model_is_configured():
    settings_default = _settings()
    assert settings_default.GEMINI_MODEL == "gemini-2.0-flash"


def test_default_groq_model_is_configured():
    settings_default = _settings()
    assert settings_default.GROQ_MODEL == "llama-3.3-70b-versatile"


def test_extraction_provider_is_a_separate_groq_instance_using_the_extraction_model():
    config = _settings(AI_PROVIDER="groq", GROQ_API_KEY="test-key", GROQ_MODEL="openai/gpt-oss-120b")
    main_provider = build_provider(config)
    extraction_provider = build_extraction_provider(config)

    assert isinstance(extraction_provider, GroqProvider)
    assert extraction_provider.model == config.GROQ_EXTRACTION_MODEL
    assert extraction_provider.model != main_provider.model
    assert extraction_provider is not main_provider


def test_extraction_provider_uses_configured_extraction_model():
    config = _settings(AI_PROVIDER="groq", GROQ_API_KEY="test-key", GROQ_EXTRACTION_MODEL="custom-extraction-model")
    extraction_provider = build_extraction_provider(config)

    assert extraction_provider.model == "custom-extraction-model"


def test_default_groq_extraction_model_is_configured():
    settings_default = _settings()
    assert settings_default.GROQ_EXTRACTION_MODEL == "openai/gpt-oss-20b"


def test_default_groq_max_tokens_and_extraction_max_tokens_are_configured():
    settings_default = _settings()
    assert settings_default.GROQ_MAX_TOKENS == 800
    assert settings_default.GROQ_EXTRACTION_MAX_TOKENS == 1200


def test_extraction_provider_reuses_main_provider_for_ollama():
    config = _settings(AI_PROVIDER="ollama")
    extraction_provider = build_extraction_provider(config)

    assert isinstance(extraction_provider, OllamaProvider)


def test_extraction_provider_reuses_main_provider_for_gemini():
    config = _settings(AI_PROVIDER="gemini", GEMINI_API_KEY="test-key")
    extraction_provider = build_extraction_provider(config)

    assert isinstance(extraction_provider, GeminiProvider)


def test_default_ai_provider_remains_ollama_for_existing_deployments():
    # Anyone with an existing .env that never set AI_PROVIDER must keep
    # getting Ollama, unchanged, after this change.
    settings_default = _settings()
    assert settings_default.AI_PROVIDER == "ollama"
