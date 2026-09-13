from backend.app.config import Settings


def test_settings_defaults():
    settings = Settings(_env_file=None)

    assert settings.APP_NAME == "Brilyx Chatbot"
    assert settings.ENVIRONMENT == "development"
    assert settings.DATABASE_URL == "sqlite:///./data/brilyx.db"
    assert settings.AI_PROVIDER == "ollama"
    assert settings.OLLAMA_BASE_URL == "http://localhost:11434"
    assert settings.OLLAMA_MODEL == "qwen2.5:3b-instruct-q4_K_M"
    assert settings.OLLAMA_TIMEOUT_SECONDS == 60


def test_settings_environment_overrides(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "custom-provider")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example-ollama:11111")
    monkeypatch.setenv("OLLAMA_MODEL", "some-other-model")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "15")

    settings = Settings(_env_file=None)

    assert settings.AI_PROVIDER == "custom-provider"
    assert settings.OLLAMA_BASE_URL == "http://example-ollama:11111"
    assert settings.OLLAMA_MODEL == "some-other-model"
    assert settings.OLLAMA_TIMEOUT_SECONDS == 15
