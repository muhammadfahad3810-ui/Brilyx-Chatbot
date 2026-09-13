"""Phase 9 — response security headers and request body-size limiting.

See backend/app/security_headers.py for why no CSP header is set here
(this API is JSON-only; CSP governs HTML documents, which this backend
never returns).
"""

import pytest

from backend.app.config import Settings, validate_production_config


def test_security_headers_present_on_a_normal_response(client):
    response = client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "permissions-policy" in response.headers


def test_security_headers_present_even_on_an_error_response(client):
    response = client.get("/api/conversations/does-not-exist")

    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"


def test_no_content_security_policy_header_is_claimed(client):
    # Deliberate: see backend/app/security_headers.py module docstring.
    response = client.get("/health")
    assert "content-security-policy" not in response.headers


def test_oversized_request_body_is_rejected_before_processing(client):
    huge_message = "a" * 100_000  # well beyond CHAT_MAX_MESSAGE_LENGTH and MAX_REQUEST_BODY_BYTES
    response = client.post("/api/chat", json={"message": huge_message})

    assert response.status_code == 413


def test_normal_sized_request_is_not_affected_by_the_body_size_guard(client):
    response = client.post("/api/conversations")
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Production configuration validation
# ---------------------------------------------------------------------------


def _settings(**overrides):
    defaults = dict(ENVIRONMENT="production", CORS_ALLOWED_ORIGINS="https://www.brilyx.com")
    defaults.update(overrides)
    return Settings(**defaults)


def test_production_config_with_explicit_origin_is_accepted():
    validate_production_config(_settings())  # must not raise


def test_development_config_is_never_validated():
    # Wildcard/empty CORS in development is allowed — only ENVIRONMENT=="production" is checked.
    dev_settings = Settings(ENVIRONMENT="development", CORS_ALLOWED_ORIGINS="*")
    validate_production_config(dev_settings)  # must not raise


def test_production_config_rejects_wildcard_cors():
    with pytest.raises(RuntimeError):
        validate_production_config(_settings(CORS_ALLOWED_ORIGINS="*"))


def test_production_config_rejects_empty_cors():
    with pytest.raises(RuntimeError):
        validate_production_config(_settings(CORS_ALLOWED_ORIGINS=""))


def test_production_config_rejects_leftover_local_dev_origin():
    with pytest.raises(RuntimeError):
        validate_production_config(_settings(CORS_ALLOWED_ORIGINS="http://127.0.0.1:5500"))


def test_production_config_error_message_does_not_leak_secrets():
    try:
        validate_production_config(_settings(CORS_ALLOWED_ORIGINS="*", SMTP_PASSWORD="super-secret"))
    except RuntimeError as exc:
        assert "super-secret" not in str(exc)
    else:
        pytest.fail("expected RuntimeError")
