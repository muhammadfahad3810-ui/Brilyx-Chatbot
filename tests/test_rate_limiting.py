"""Phase 9 — rate limiting.

Two dependency-free, in-process, sliding-window limiters (see
backend/app/rate_limit.py) protect the two write endpoints worth
protecting: `/api/chat` (Ollama-backed, expensive) and
`POST /api/conversations` (cheap, but unbounded creation is still a DB-
write flood vector). Every test here uses a *fresh* `InMemoryRateLimiter`
instance monkeypatched in place of the module-level singleton, rather than
exhausting the real default limits — that keeps this file fast and
independent of whatever default numbers `Settings` happens to configure.
"""

from backend.app.ai.base import AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.main import app
from backend.app.rate_limit import InMemoryRateLimiter
from backend.app.routers import chat as chat_router
from backend.app.routers import conversations as conversations_router


class _FakeOrchestrator:
    def chat(self, message, history=None, context=None):
        return AIResponse(text="ok", provider="fake", model="fake")

    def extract(self, system_prompt, message):
        return AIResponse(text="{}", provider="fake", model="fake")


def teardown_function():
    app.dependency_overrides.pop(get_orchestrator, None)


# ---------------------------------------------------------------------------
# Pure limiter unit tests
# ---------------------------------------------------------------------------


def test_limiter_allows_up_to_the_configured_maximum():
    limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
    assert limiter.allow("key") is True
    assert limiter.allow("key") is True
    assert limiter.allow("key") is True
    assert limiter.allow("key") is False


def test_limiter_tracks_keys_independently():
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True  # different key, unaffected by "a"'s usage
    assert limiter.allow("a") is False
    assert limiter.allow("b") is False


def test_limiter_does_not_permanently_block_once_the_window_passes():
    fake_time = [0.0]
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=10, time_func=lambda: fake_time[0])

    assert limiter.allow("key") is True
    assert limiter.allow("key") is False

    fake_time[0] = 10.1  # window has now elapsed
    assert limiter.allow("key") is True


def test_limiter_never_relies_on_a_client_supplied_timestamp():
    # The limiter's public API takes no timestamp argument at all — the
    # only way to influence timing is the server-side clock it was built
    # with. This test simply pins that contract down.
    import inspect

    signature = inspect.signature(InMemoryRateLimiter.allow)
    assert list(signature.parameters) == ["self", "key"]


# ---------------------------------------------------------------------------
# /api/chat integration
# ---------------------------------------------------------------------------


def test_chat_endpoint_allows_normal_usage(client, monkeypatch):
    monkeypatch.setattr(chat_router, "CHAT_RATE_LIMITER", InMemoryRateLimiter(max_requests=5, window_seconds=60))
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    for _ in range(5):
        response = client.post("/api/chat", json={"message": "Hi"})
        assert response.status_code == 200


def test_chat_endpoint_returns_429_once_exceeded(client, monkeypatch):
    monkeypatch.setattr(chat_router, "CHAT_RATE_LIMITER", InMemoryRateLimiter(max_requests=2, window_seconds=60))
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    assert client.post("/api/chat", json={"message": "one"}).status_code == 200
    assert client.post("/api/chat", json={"message": "two"}).status_code == 200
    third = client.post("/api/chat", json={"message": "three"})

    assert third.status_code == 429
    assert "too many requests" in third.json()["detail"].lower()


def test_rate_limit_error_does_not_leak_internal_details(client, monkeypatch):
    monkeypatch.setattr(chat_router, "CHAT_RATE_LIMITER", InMemoryRateLimiter(max_requests=1, window_seconds=60))
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    client.post("/api/chat", json={"message": "one"})
    limited = client.post("/api/chat", json={"message": "two"})

    assert limited.status_code == 429
    for forbidden in ("InMemoryRateLimiter", "deque", "Traceback", "backend/app", "backend\\app"):
        assert forbidden not in limited.text


def test_limiter_recovers_after_the_window_so_visitors_are_not_permanently_blocked(client, monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(
        chat_router, "CHAT_RATE_LIMITER", InMemoryRateLimiter(max_requests=1, window_seconds=30, time_func=lambda: fake_time[0])
    )
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    assert client.post("/api/chat", json={"message": "one"}).status_code == 200
    assert client.post("/api/chat", json={"message": "two"}).status_code == 429

    fake_time[0] += 31
    assert client.post("/api/chat", json={"message": "three"}).status_code == 200


# ---------------------------------------------------------------------------
# POST /api/conversations integration
# ---------------------------------------------------------------------------


def test_conversation_creation_allows_normal_usage(client, monkeypatch):
    monkeypatch.setattr(
        conversations_router, "CONVERSATION_CREATE_RATE_LIMITER", InMemoryRateLimiter(max_requests=3, window_seconds=60)
    )

    for _ in range(3):
        assert client.post("/api/conversations").status_code == 201


def test_conversation_creation_returns_429_once_exceeded(client, monkeypatch):
    monkeypatch.setattr(
        conversations_router, "CONVERSATION_CREATE_RATE_LIMITER", InMemoryRateLimiter(max_requests=2, window_seconds=60)
    )

    assert client.post("/api/conversations").status_code == 201
    assert client.post("/api/conversations").status_code == 201
    third = client.post("/api/conversations")

    assert third.status_code == 429


def test_two_different_client_ips_have_independent_chat_limits(client, monkeypatch):
    # TestClient always reports the same client host, so this exercises the
    # limiter's per-key isolation directly rather than through two real
    # sockets — see test_limiter_tracks_keys_independently for the pure
    # version of this guarantee.
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    monkeypatch.setattr(chat_router, "CHAT_RATE_LIMITER", limiter)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("5.6.7.8") is True
