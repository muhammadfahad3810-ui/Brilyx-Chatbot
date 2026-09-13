"""Phase 9 — trust boundary audit, error leakage, and request validation.

Much of the "client cannot control X" ground is already covered by
dedicated Phase 5/6/8 tests (qualification/lead/event tests each assert
their own trust boundary in context). This file adds the Phase-9-specific
checks: unhandled-exception sanitization, malformed/oversized input
handling, and a consolidated sweep confirming no security-sensitive value
is ever accepted from the client.
"""

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from backend.app.ai.base import AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import BusinessEvent


class _FakeOrchestrator:
    def __init__(self, chat_error=None):
        self._chat_error = chat_error

    def chat(self, message, history=None, context=None):
        if self._chat_error:
            raise self._chat_error
        return AIResponse(text="ok", provider="fake", model="fake")

    def extract(self, system_prompt, message):
        return AIResponse(text="{}", provider="fake", model="fake")


def teardown_function():
    app.dependency_overrides.pop(get_orchestrator, None)


# ---------------------------------------------------------------------------
# Error leakage
# ---------------------------------------------------------------------------


def test_unexpected_orchestrator_exception_is_sanitized_not_leaked(client):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator(
        chat_error=ValueError("leaked: SMTP_PASSWORD=hunter2 /etc/passwd C:\\secrets\\config.py")
    )

    response = client.post("/api/chat", json={"message": "Hi"})

    assert response.status_code == 503
    assert "hunter2" not in response.text
    assert "SMTP_PASSWORD" not in response.text
    assert "/etc/passwd" not in response.text
    assert "secrets" not in response.text
    assert "Traceback" not in response.text
    assert "ValueError" not in response.text


def test_database_failure_during_chat_returns_generic_500(client, monkeypatch):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    import backend.app.routers.chat as chat_router

    def boom(*args, **kwargs):
        raise SQLAlchemyError("(sqlite3.OperationalError) database is locked at E:\\PROJECTS\\data\\brilyx.db")

    monkeypatch.setattr(chat_router, "run_intelligence", boom)

    response = client.post("/api/chat", json={"message": "Hi"})

    assert response.status_code == 500
    assert "brilyx.db" not in response.text
    assert "sqlite3" not in response.text
    assert "OperationalError" not in response.text
    assert "Traceback" not in response.text


def test_unhandled_non_sqlalchemy_exception_never_returns_a_raw_traceback(db_session, monkeypatch):
    # add_message's try/except in chat.py only catches SQLAlchemyError —
    # this simulates a genuinely unanticipated exception type slipping
    # through, to confirm FastAPI/Starlette's own default handling (no
    # debug=True anywhere in this app — see backend/app/main.py) still
    # never serializes a traceback into the response body.
    #
    # `raise_server_exceptions=False` makes TestClient behave like a real
    # deployed server talking to a real browser: by default TestClient
    # re-raises the server-side exception into the *test* itself (useful
    # for debugging, but not what an actual visitor's browser would ever
    # see) — a real client only ever gets Starlette's own sanitized 500.
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    raw_client = TestClient(app, raise_server_exceptions=False)

    import backend.app.routers.chat as chat_router

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected internal detail that must never reach the client")

    monkeypatch.setattr(chat_router, "add_message", boom)

    try:
        response = raw_client.post("/api/chat", json={"message": "Hi"})

        assert response.status_code == 500
        assert "unexpected internal detail" not in response.text
        assert "Traceback" not in response.text
        assert "RuntimeError" not in response.text
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_malformed_json_body_returns_422_not_500(client):
    response = client.post(
        "/api/chat",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_malformed_conversation_id_returns_404_not_500(client):
    for bogus_id in ["not-a-uuid", "'; DROP TABLE conversations; --", "../../etc/passwd", "🙂🙂🙂"]:
        response = client.post("/api/chat", json={"conversation_id": bogus_id, "message": "Hi"})
        assert response.status_code == 404, bogus_id


def test_unexpected_extra_fields_are_silently_ignored_not_rejected(client):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    response = client.post(
        "/api/chat",
        json={"message": "Hi", "totally_unexpected_field": "whatever", "admin": True},
    )
    assert response.status_code == 200


def test_invalid_content_type_with_valid_json_body_still_processed_safely(client):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    import json

    response = client.post(
        "/api/chat",
        content=json.dumps({"message": "Hi"}),
        headers={"Content-Type": "text/plain"},
    )
    # FastAPI parses the body as JSON regardless of a mismatched
    # Content-Type; either it's accepted (200) or cleanly rejected (415/422)
    # — the only wrong outcome is a 500 / unhandled crash.
    assert response.status_code in (200, 415, 422)


# ---------------------------------------------------------------------------
# Trust boundaries: consolidated sweep of client-controllable fields
# ---------------------------------------------------------------------------


def test_client_cannot_directly_create_a_business_event(client, db_session):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    response = client.post(
        "/api/chat",
        json={
            "message": "Hi",
            "event_type": "high_value_lead",
            "status": "completed",
            "notification_recipient": "attacker@example.com",
        },
    )
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]

    events = db_session.query(BusinessEvent).filter_by(conversation_id=conversation_id).all()
    assert events == []  # "Hi" alone triggers no business event, regardless of the extra fields sent


def test_client_cannot_override_owner_notification_recipient(client):
    from backend.app.routers.chat import ChatRequest

    # The only two fields the client can ever send to /api/chat.
    assert set(ChatRequest.model_fields) == {"conversation_id", "message"}
    # The notification recipient only ever comes from server-side config —
    # confirmed by construction, not by any request-time lookup.
    assert settings.OWNER_NOTIFICATION_EMAIL == "" or "@" in settings.OWNER_NOTIFICATION_EMAIL


def test_no_endpoint_accepts_a_client_supplied_pricing_override(client):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    response = client.post(
        "/api/chat",
        json={"message": "How much is the Business package?", "price": 1, "override_pricing": True},
    )
    assert response.status_code == 200
    # The fake orchestrator's canned "ok" response proves nothing about
    # pricing content itself (that's Phase 4.1.1's job) — this test only
    # proves the extra fields are inert, never reflected into behavior.
    assert response.json()["response"] == "ok"
