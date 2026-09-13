def test_state_for_fresh_conversation_returns_safe_defaults(client):
    created = client.post("/api/conversations").json()

    response = client.get(
        f"/api/conversations/{created['conversation_id']}/state",
        headers={"X-Conversation-Token": created["session_id"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == created["conversation_id"]
    assert body["intent"] == "unknown"
    assert body["business_type"] == "unknown"
    assert body["requested_service"] == "unknown"
    assert body["requirements"] == []
    assert body["demo_requested"] is False
    assert body["human_handoff_requested"] is False
    assert body["handoff_recommended"] is False
    assert body["confidence"] == 0.0
    assert body["updated_at"] is None


def test_state_for_unknown_conversation_returns_404(client):
    response = client.get("/api/conversations/does-not-exist/state")

    assert response.status_code == 404


def test_state_response_never_exposes_system_prompt_or_internal_fields(client):
    created = client.post("/api/conversations").json()

    response = client.get(
        f"/api/conversations/{created['conversation_id']}/state",
        headers={"X-Conversation-Token": created["session_id"]},
    )

    body = response.json()
    assert "system_prompt" not in body
    assert "correction_count" not in body
    assert "knowledge" not in response.text.lower()
