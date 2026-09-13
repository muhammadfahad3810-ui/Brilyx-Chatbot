import pytest

from backend.app.ai.base import AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.config import settings
from backend.app.main import app


class SequencedFakeOrchestrator:
    """Returns a distinct, predictable reply for each call and records the history it received."""

    def __init__(self):
        self.calls = []
        self._counter = 0

    def chat(self, message, history=None, context=None):
        self._counter += 1
        self.calls.append({"message": message, "history": list(history or []), "context": context})
        return AIResponse(text=f"reply-{self._counter}", provider="ollama", model="m")

    def extract(self, system_prompt, message):
        return AIResponse(text="{}", provider="fake", model="fake")


@pytest.fixture(autouse=True)
def clear_orchestrator_override():
    yield
    app.dependency_overrides.pop(get_orchestrator, None)


def test_history_limit_restricts_messages_sent_to_provider(client, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_HISTORY_MAX_MESSAGES", 4)
    fake = SequencedFakeOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: fake

    conversation_id = client.post("/api/conversations").json()["conversation_id"]

    # Four user/assistant turns -> 8 stored messages total.
    for i in range(1, 5):
        response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": f"message-{i}"})
        assert response.status_code == 200

    last_call_history = fake.calls[-1]["history"]

    # Limit is 4, so only the 4 most recent prior messages should be sent,
    # not all 6 that existed before the 4th user message.
    assert len(last_call_history) == 4
    contents = [m.content for m in last_call_history]
    assert contents == ["message-2", "reply-2", "message-3", "reply-3"]


def test_history_is_chronological_and_system_prompt_is_orchestrator_responsibility(client, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_HISTORY_MAX_MESSAGES", 20)
    fake = SequencedFakeOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: fake

    conversation_id = client.post("/api/conversations").json()["conversation_id"]
    for i in range(1, 4):
        client.post("/api/chat", json={"conversation_id": conversation_id, "message": f"message-{i}"})

    last_call_history = fake.calls[-1]["history"]
    contents = [m.content for m in last_call_history]
    # Chronological order, oldest first, with no gaps or reordering.
    assert contents == ["message-1", "reply-1", "message-2", "reply-2"]

    # The chat router/history layer never builds the *base* system prompt
    # itself — that responsibility stays with the AIOrchestrator (see
    # backend/app/ai/orchestrator.py). It does pass a `context` string (the
    # structured conversation-intelligence summary — see
    # backend/app/intelligence/service.py) alongside `message`/`history`,
    # which the orchestrator appends to its own cached system prompt.
    assert set(fake.calls[-1].keys()) == {"message", "history", "context"}


def test_history_limit_of_one_still_returns_most_recent_message_only(client, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_HISTORY_MAX_MESSAGES", 1)
    fake = SequencedFakeOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: fake

    conversation_id = client.post("/api/conversations").json()["conversation_id"]
    client.post("/api/chat", json={"conversation_id": conversation_id, "message": "message-1"})
    client.post("/api/chat", json={"conversation_id": conversation_id, "message": "message-2"})

    last_call_history = fake.calls[-1]["history"]
    assert len(last_call_history) == 1
    assert last_call_history[0].content == "reply-1"
