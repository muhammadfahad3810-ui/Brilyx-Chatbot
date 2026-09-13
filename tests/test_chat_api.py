import pytest

from backend.app.ai.base import AIProviderError, AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.config import settings
from backend.app.main import app


class FakeOrchestrator:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    @property
    def called(self):
        return len(self.calls) > 0

    @property
    def last_call(self):
        return self.calls[-1] if self.calls else None

    def chat(self, message, history=None, context=None):
        self.calls.append({"message": message, "history": history, "context": context})
        if self._error:
            raise self._error
        return self._response

    def extract(self, system_prompt, message):
        return AIResponse(text="{}", provider="fake", model="fake")


@pytest.fixture(autouse=True)
def clear_orchestrator_override():
    yield
    app.dependency_overrides.pop(get_orchestrator, None)


def _override_orchestrator(fake):
    app.dependency_overrides[get_orchestrator] = lambda: fake


def test_chat_without_conversation_id_auto_creates_conversation(client):
    fake = FakeOrchestrator(response=AIResponse(text="Hi there!", provider="ollama", model="qwen2.5:3b-instruct-q4_K_M"))
    _override_orchestrator(fake)

    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]
    assert body["response"] == "Hi there!"
    assert body["provider"] == "ollama"
    assert body["model"] == "qwen2.5:3b-instruct-q4_K_M"


def test_first_message_is_saved_and_history_is_empty(client):
    fake = FakeOrchestrator(response=AIResponse(text="Welcome!", provider="ollama", model="m"))
    _override_orchestrator(fake)

    response = client.post("/api/chat", json={"message": "Hello"})
    body = response.json()
    conversation_id = body["conversation_id"]
    headers = {"X-Conversation-Token": body["session_id"]}

    assert fake.last_call["history"] == []

    fetched = client.get(f"/api/conversations/{conversation_id}", headers=headers).json()
    assert [m["role"] for m in fetched["messages"]] == ["user", "assistant"]
    assert fetched["messages"][0]["content"] == "Hello"
    assert fetched["messages"][1]["content"] == "Welcome!"


def test_second_message_receives_prior_history_without_duplicating_current_message(client):
    fake = FakeOrchestrator(response=AIResponse(text="First reply", provider="ollama", model="m"))
    _override_orchestrator(fake)
    first = client.post("/api/chat", json={"message": "First message"})
    first_body = first.json()
    conversation_id = first_body["conversation_id"]
    headers = {"X-Conversation-Token": first_body["session_id"]}

    fake._response = AIResponse(text="Second reply", provider="ollama", model="m")
    second = client.post("/api/chat", json={"conversation_id": conversation_id, "message": "Second message"})

    assert second.status_code == 200
    history_sent = fake.last_call["history"]
    assert len(history_sent) == 2
    assert history_sent[0].role == "user"
    assert history_sent[0].content == "First message"
    assert history_sent[1].role == "assistant"
    assert history_sent[1].content == "First reply"
    # The current message must not appear inside the history list itself.
    assert all(m.content != "Second message" for m in history_sent)
    assert fake.last_call["message"] == "Second message"

    fetched = client.get(f"/api/conversations/{conversation_id}", headers=headers).json()
    contents = [m["content"] for m in fetched["messages"]]
    assert contents == ["First message", "First reply", "Second message", "Second reply"]


def test_chat_with_unknown_conversation_id_returns_404(client):
    fake = FakeOrchestrator(response=AIResponse(text="x", provider="ollama", model="m"))
    _override_orchestrator(fake)

    response = client.post("/api/chat", json={"conversation_id": "does-not-exist", "message": "Hello"})

    assert response.status_code == 404
    assert fake.called is False


def test_chat_on_closed_conversation_returns_409_and_does_not_call_provider(client):
    fake = FakeOrchestrator(response=AIResponse(text="x", provider="ollama", model="m"))
    _override_orchestrator(fake)

    created = client.post("/api/conversations").json()
    conversation_id = created["conversation_id"]
    headers = {"X-Conversation-Token": created["session_id"]}
    client.post(f"/api/conversations/{conversation_id}/close", headers=headers)

    response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": "Hello"})

    assert response.status_code == 409
    assert fake.called is False


def test_provider_failure_returns_503_and_does_not_save_fake_assistant_message(client):
    fake = FakeOrchestrator(error=AIProviderError("Ollama is down"))
    _override_orchestrator(fake)

    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "Brilyx AI is temporarily unavailable. Please try again shortly."
    assert "Ollama is down" not in response.text
    assert "Traceback" not in response.text

    # The response has no conversation_id on failure, but we can find it by
    # creating one more turn isn't possible; instead assert via a fresh
    # conversation created explicitly, replaying the same failure.
    created = client.post("/api/conversations").json()
    conversation_id = created["conversation_id"]
    headers = {"X-Conversation-Token": created["session_id"]}
    failing_response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": "Hello again"})
    assert failing_response.status_code == 503

    fetched = client.get(f"/api/conversations/{conversation_id}", headers=headers).json()
    roles = [m["role"] for m in fetched["messages"]]
    assert roles == ["user"]  # user message persisted, no assistant message


def test_chat_rejects_empty_message(client):
    fake = FakeOrchestrator(response=AIResponse(text="x", provider="ollama", model="m"))
    _override_orchestrator(fake)

    response = client.post("/api/chat", json={"message": ""})

    assert response.status_code == 422


def test_chat_rejects_whitespace_only_message(client):
    fake = FakeOrchestrator(response=AIResponse(text="x", provider="ollama", model="m"))
    _override_orchestrator(fake)

    response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 422


def test_chat_rejects_oversized_message(client):
    fake = FakeOrchestrator(response=AIResponse(text="x", provider="ollama", model="m"))
    _override_orchestrator(fake)

    too_long = "a" * (settings.CHAT_MAX_MESSAGE_LENGTH + 1)
    response = client.post("/api/chat", json={"message": too_long})

    assert response.status_code == 422
