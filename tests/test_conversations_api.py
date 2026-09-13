def _headers(created: dict) -> dict:
    return {"X-Conversation-Token": created["session_id"]}


def test_create_conversation_returns_active_status_and_ids(client):
    response = client.post("/api/conversations")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active"
    assert body["conversation_id"]
    assert body["session_id"]
    assert body["conversation_id"] != body["session_id"]


def test_create_conversation_generates_unique_ids(client):
    first = client.post("/api/conversations").json()
    second = client.post("/api/conversations").json()

    assert first["conversation_id"] != second["conversation_id"]
    assert first["session_id"] != second["session_id"]


def test_get_conversation_returns_empty_message_list_for_new_conversation(client):
    created = client.post("/api/conversations").json()

    response = client.get(f"/api/conversations/{created['conversation_id']}", headers=_headers(created))

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == created["conversation_id"]
    assert body["session_id"] == created["session_id"]
    assert body["status"] == "active"
    assert body["messages"] == []


def test_get_nonexistent_conversation_returns_404(client):
    response = client.get("/api/conversations/does-not-exist")

    assert response.status_code == 404
    assert "does-not-exist" not in response.text


def test_close_conversation_changes_status_to_closed(client):
    created = client.post("/api/conversations").json()

    response = client.post(f"/api/conversations/{created['conversation_id']}/close", headers=_headers(created))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "closed"

    fetched = client.get(f"/api/conversations/{created['conversation_id']}", headers=_headers(created)).json()
    assert fetched["status"] == "closed"


def test_closing_already_closed_conversation_is_idempotent(client):
    created = client.post("/api/conversations").json()
    conversation_id = created["conversation_id"]

    first_close = client.post(f"/api/conversations/{conversation_id}/close", headers=_headers(created))
    second_close = client.post(f"/api/conversations/{conversation_id}/close", headers=_headers(created))

    assert first_close.status_code == 200
    assert second_close.status_code == 200
    assert first_close.json()["status"] == "closed"
    assert second_close.json()["status"] == "closed"


def test_close_nonexistent_conversation_returns_404(client):
    response = client.post("/api/conversations/does-not-exist/close")

    assert response.status_code == 404
