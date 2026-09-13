"""Phase 9 — conversation ownership / IDOR protection.

`Conversation.session_id` (already returned by every conversation-creating
endpoint since Phase 3) doubles as the access token: `conversation_id` is
the public resource identifier, `session_id` is the secret companion that
proves ownership. See docs/security.md "Conversation Access Control" for
the full design rationale, including why `/api/chat` itself is
deliberately NOT gated by this token (it stays on the original,
documented, widely-tested Phase 3 contract).
"""

import logging

from backend.app.ai.base import AIResponse
from backend.app.ai.orchestrator import get_orchestrator
from backend.app.main import app

PROTECTED_GET_PATHS = ["", "/state", "/qualification", "/lead"]


class _FakeOrchestrator:
    def chat(self, message, history=None, context=None):
        return AIResponse(text="ok", provider="fake", model="fake")

    def extract(self, system_prompt, message):
        return AIResponse(text="{}", provider="fake", model="fake")


def teardown_function():
    app.dependency_overrides.pop(get_orchestrator, None)


def _create(client) -> dict:
    return client.post("/api/conversations").json()


def test_legitimate_access_with_correct_token_succeeds(client):
    created = _create(client)
    headers = {"X-Conversation-Token": created["session_id"]}

    for suffix in PROTECTED_GET_PATHS:
        response = client.get(f"/api/conversations/{created['conversation_id']}{suffix}", headers=headers)
        assert response.status_code in (200, 404), suffix
        # /lead legitimately 404s with no lead captured yet; the others must be 200.
        if suffix != "/lead":
            assert response.status_code == 200, suffix


def test_wrong_access_credential_is_rejected(client):
    created = _create(client)
    other = _create(client)
    headers = {"X-Conversation-Token": other["session_id"]}  # a real token, just not this conversation's

    for suffix in PROTECTED_GET_PATHS:
        response = client.get(f"/api/conversations/{created['conversation_id']}{suffix}", headers=headers)
        assert response.status_code == 404, suffix


def test_missing_access_credential_is_rejected(client):
    created = _create(client)

    for suffix in PROTECTED_GET_PATHS:
        response = client.get(f"/api/conversations/{created['conversation_id']}{suffix}")
        assert response.status_code == 404, suffix


def test_forged_access_credential_is_rejected(client):
    created = _create(client)
    forged_headers = {"X-Conversation-Token": "00000000-0000-0000-0000-000000000000"}

    for suffix in PROTECTED_GET_PATHS:
        response = client.get(f"/api/conversations/{created['conversation_id']}{suffix}", headers=forged_headers)
        assert response.status_code == 404, suffix


def test_cannot_access_another_visitors_conversation_history(client):
    mine = _create(client)
    someone_elses = _create(client)
    my_headers = {"X-Conversation-Token": mine["session_id"]}

    response = client.get(f"/api/conversations/{someone_elses['conversation_id']}", headers=my_headers)

    assert response.status_code == 404


def test_cannot_close_another_visitors_conversation(client):
    mine = _create(client)
    someone_elses = _create(client)
    my_headers = {"X-Conversation-Token": mine["session_id"]}

    response = client.post(f"/api/conversations/{someone_elses['conversation_id']}/close", headers=my_headers)
    assert response.status_code == 404

    # And it must genuinely remain open — not merely have returned an error
    # while quietly closing it anyway.
    check = client.get(
        f"/api/conversations/{someone_elses['conversation_id']}",
        headers={"X-Conversation-Token": someone_elses["session_id"]},
    )
    assert check.json()["status"] == "active"


def test_closing_own_conversation_still_works_with_correct_token(client):
    created = _create(client)
    headers = {"X-Conversation-Token": created["session_id"]}

    response = client.post(f"/api/conversations/{created['conversation_id']}/close", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "closed"


def test_unauthorized_response_never_reveals_whether_the_conversation_exists(client):
    created = _create(client)
    real_but_wrong = client.get(
        f"/api/conversations/{created['conversation_id']}/state",
        headers={"X-Conversation-Token": "wrong-token"},
    )
    truly_nonexistent = client.get(
        "/api/conversations/00000000-0000-0000-0000-000000000000/state",
        headers={"X-Conversation-Token": "wrong-token"},
    )

    assert real_but_wrong.status_code == truly_nonexistent.status_code == 404
    assert real_but_wrong.json() == truly_nonexistent.json()


# ---------------------------------------------------------------------------
# Server-side-only observability: the HTTP response for "doesn't exist" and
# "wrong/missing token" must stay identical (asserted above), but operators
# must still be able to tell the two apart from logs alone. See
# backend/app/services/conversation_service.py.
# ---------------------------------------------------------------------------


def test_wrong_token_logs_invalid_token_reason_not_not_found(client, caplog):
    created = _create(client)

    with caplog.at_level(logging.INFO, logger="brilyx.conversations"):
        response = client.get(
            f"/api/conversations/{created['conversation_id']}/lead",
            headers={"X-Conversation-Token": "wrong-token"},
        )

    assert response.status_code == 404
    messages = [record.getMessage() for record in caplog.records]
    assert any("reason=invalid_token" in message and created["conversation_id"] in message for message in messages)
    assert not any("reason=not_found" in message for message in messages)


def test_missing_token_logs_invalid_token_reason(client, caplog):
    created = _create(client)

    with caplog.at_level(logging.INFO, logger="brilyx.conversations"):
        response = client.get(f"/api/conversations/{created['conversation_id']}/lead")

    assert response.status_code == 404
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reason=invalid_token" in message and "token_provided=False" in message for message in messages
    )


def test_truly_nonexistent_conversation_logs_not_found_reason(client, caplog):
    fake_id = "00000000-0000-0000-0000-000000000000"

    with caplog.at_level(logging.INFO, logger="brilyx.conversations"):
        response = client.get(
            f"/api/conversations/{fake_id}/lead",
            headers={"X-Conversation-Token": "irrelevant"},
        )

    assert response.status_code == 404
    messages = [record.getMessage() for record in caplog.records]
    assert any("reason=not_found" in message and fake_id in message for message in messages)
    assert not any("reason=invalid_token" in message for message in messages)


def test_access_denial_logs_never_contain_the_real_or_supplied_token_value(client, caplog):
    created = _create(client)
    real_token = created["session_id"]
    wrong_token = "a-clearly-wrong-token-value"

    with caplog.at_level(logging.INFO, logger="brilyx.conversations"):
        client.get(
            f"/api/conversations/{created['conversation_id']}/lead",
            headers={"X-Conversation-Token": wrong_token},
        )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert real_token not in log_text
    assert wrong_token not in log_text


def test_session_id_is_returned_by_every_conversation_creating_endpoint(client):
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()

    from_create = client.post("/api/conversations").json()
    assert from_create["session_id"]

    from_chat = client.post("/api/chat", json={"message": "Hi"}).json()
    assert from_chat["session_id"]
