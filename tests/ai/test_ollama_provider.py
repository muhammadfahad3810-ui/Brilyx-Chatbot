import httpx
import pytest

from backend.app.ai import ollama as ollama_module
from backend.app.ai.base import AIProviderError, ChatMessage
from backend.app.ai.ollama import OllamaProvider


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, json_error=None):
        self.status_code = status_code
        self._json_data = json_data
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._json_data


@pytest.fixture
def provider():
    return OllamaProvider(
        base_url="http://localhost:11434",
        model="qwen2.5:3b-instruct-q4_K_M",
        timeout_seconds=5,
    )


def test_successful_response(monkeypatch, provider):
    def fake_post(url, json, timeout):
        assert url == "http://localhost:11434/api/chat"
        assert json["model"] == "qwen2.5:3b-instruct-q4_K_M"
        assert json["stream"] is False
        return FakeResponse(status_code=200, json_data={"message": {"content": "Hello from Qwen!"}})

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    result = provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])

    assert result.text == "Hello from Qwen!"
    assert result.provider == "ollama"
    assert result.model == "qwen2.5:3b-instruct-q4_K_M"


def test_timeout_raises_ai_provider_error(monkeypatch, provider):
    def fake_post(url, json, timeout):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_connection_error_raises_ai_provider_error(monkeypatch, provider):
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_http_error_status_raises_ai_provider_error(monkeypatch, provider):
    def fake_post(url, json, timeout):
        return FakeResponse(status_code=500, json_data={})

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_malformed_response_missing_message_raises_ai_provider_error(monkeypatch, provider):
    def fake_post(url, json, timeout):
        return FakeResponse(status_code=200, json_data={"unexpected": "shape"})

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_malformed_response_invalid_json_raises_ai_provider_error(monkeypatch, provider):
    def fake_post(url, json, timeout):
        return FakeResponse(status_code=200, json_error=ValueError("bad json"))

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])


def test_empty_content_raises_ai_provider_error(monkeypatch, provider):
    def fake_post(url, json, timeout):
        return FakeResponse(status_code=200, json_data={"message": {"content": "   "}})

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)

    with pytest.raises(AIProviderError):
        provider.generate(system_prompt="sys", messages=[ChatMessage(role="user", content="hi")])
