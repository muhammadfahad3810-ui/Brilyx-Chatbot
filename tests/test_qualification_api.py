from backend.app.ai.orchestrator import get_orchestrator
from backend.app.main import app


class RecordingFakeOrchestrator:
    """Same fake used by tests/test_chat_intelligence.py — kept local and minimal here."""

    def __init__(self, chat_response_text="ok", extraction_json="{}"):
        self._chat_response_text = chat_response_text
        self._extraction_json = extraction_json
        self.chat_calls = []

    def chat(self, message, history=None, context=None):
        from backend.app.ai.base import AIResponse

        self.chat_calls.append({"message": message, "context": context})
        return AIResponse(text=self._chat_response_text, provider="fake", model="fake")

    def extract(self, system_prompt, message):
        from backend.app.ai.base import AIResponse

        return AIResponse(text=self._extraction_json, provider="fake", model="fake")


def _override(fake):
    app.dependency_overrides[get_orchestrator] = lambda: fake


def teardown_function():
    app.dependency_overrides.pop(get_orchestrator, None)


# conversation_id -> session_id (the access token — see docs/security.md
# "Conversation Access Control"), captured automatically so individual
# test bodies never need to juggle it themselves.
_TOKENS: dict[str, str] = {}


def _send(client, conversation_id, message):
    payload = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    body = response.json()
    _TOKENS[body["conversation_id"]] = body["session_id"]
    return body["conversation_id"]


def _qualification(client, conversation_id):
    headers = {"X-Conversation-Token": _TOKENS.get(conversation_id, "")}
    response = client.get(f"/api/conversations/{conversation_id}/qualification", headers=headers)
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------------------
# Debug endpoint basics
# ---------------------------------------------------------------------------


def test_qualification_for_fresh_conversation_is_zero_low(client):
    created = client.post("/api/conversations").json()
    _TOKENS[created["conversation_id"]] = created["session_id"]

    body = _qualification(client, created["conversation_id"])

    assert body["conversation_id"] == created["conversation_id"]
    assert body["score"] == 0
    assert body["level"] == "low"
    assert body["reasons"] == []


def test_qualification_for_unknown_conversation_returns_404(client):
    response = client.get("/api/conversations/does-not-exist/qualification")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# S: multi-turn progression
# ---------------------------------------------------------------------------


def test_multi_turn_progression_is_deterministic_and_monotonic(client):
    _override(RecordingFakeOrchestrator())

    conversation_id = _send(client, None, "Hi")
    q1 = _qualification(client, conversation_id)
    assert (q1["score"], q1["level"]) == (0, "low")

    _send(client, conversation_id, "I run a dental clinic.")
    q2 = _qualification(client, conversation_id)
    assert (q2["score"], q2["level"]) == (15, "low")

    _send(client, conversation_id, "We receive many repetitive questions via whatsapp.")
    q3 = _qualification(client, conversation_id)
    assert (q3["score"], q3["level"]) == (35, "medium")

    _send(client, conversation_id, "I need a WhatsApp AI agent.")
    q4 = _qualification(client, conversation_id)
    assert (q4["score"], q4["level"]) == (50, "medium")

    _send(client, conversation_id, "How much does it cost?")
    q5 = _qualification(client, conversation_id)
    assert (q5["score"], q5["level"]) == (60, "high")

    _send(client, conversation_id, "I'd like a demo.")
    q6 = _qualification(client, conversation_id)
    assert (q6["score"], q6["level"]) == (80, "high")

    scores = [q1["score"], q2["score"], q3["score"], q4["score"], q5["score"], q6["score"]]
    assert scores == sorted(scores)  # never decreases as more is learned


def test_repeating_the_same_signal_does_not_inflate_the_score(client):
    _override(RecordingFakeOrchestrator())

    conversation_id = _send(client, None, "I run a dental clinic.")
    _send(client, conversation_id, "I run a dental clinic, yes, a dental clinic.")
    _send(client, conversation_id, "Again, dental clinic.")

    body = _qualification(client, conversation_id)
    assert body["score"] == 15  # business signal counted once, not three times


# ---------------------------------------------------------------------------
# Worked examples (Q/R) through the real chat flow
# ---------------------------------------------------------------------------


def test_restaurant_example_through_chat_flow(client):
    _override(RecordingFakeOrchestrator())

    conversation_id = _send(client, None, "I run a restaurant.")
    _send(client, conversation_id, "My restaurant gets lots of repetitive questions from customers.")

    body = _qualification(client, conversation_id)
    assert body["score"] >= 15  # at least business identified
    assert body["level"] in ("low", "medium")


# ---------------------------------------------------------------------------
# P: pricing interest must be visitor-driven, not assistant-driven
# ---------------------------------------------------------------------------


def test_assistant_mentioning_pricing_does_not_award_pricing_signal(client):
    _override(RecordingFakeOrchestrator(chat_response_text="Our Business package costs $99/month."))

    conversation_id = _send(client, None, "I need business automation.")

    body = _qualification(client, conversation_id)
    assert not any("pricing" in r.lower() for r in body["reasons"])


# ---------------------------------------------------------------------------
# Score must never leak into the visitor-facing chat response
# ---------------------------------------------------------------------------


def test_chat_response_never_exposes_score_or_level(client):
    _override(RecordingFakeOrchestrator())

    response = client.post("/api/chat", json={"message": "I'd like a demo for my dental clinic."})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"conversation_id", "session_id", "response", "provider", "model"}
    assert "score" not in response.text.lower()
    assert "qualified" not in response.text.lower()
